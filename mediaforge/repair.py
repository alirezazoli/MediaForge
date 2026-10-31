"""Automatic video repair: measure the damage, then fix it.

Three jobs, all free and all offline:

  1. find the dead air   -- stretches that are both black and silent, and cut them out
  2. measure the noise   -- and pick a denoise strength from the measurement, not a guess
  3. raise the detail    -- unsharp for a soft source, Real-ESRGAN when it is also small

The measuring is the point. "Denoise" as a slider the user drags blindly either does
nothing or turns faces into plastic; a number taken off the actual frames lands on a
setting that suits this file. Everything measured is reported in words, and every
recommendation can be overridden before anything is written.

Nothing here modifies the source. A repair always writes a new file.
"""

from __future__ import annotations

import math
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import diagnose, ffmpeg_tools
from .ffmpeg_tools import FfmpegCommand, FfmpegLocator, MediaInfo

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

Progress = Callable[[float, str], None]
Cancel = Callable[[], bool]


def _noop(fraction: float, message: str) -> None:
    return None


# ======================================================================================
# Detection
# ======================================================================================

# blackdetect's own defaults are deliberately strict; these are the ones that match what
# a person calls "black": a frame is black if 98% of its pixels sit under 10% luma.
BLACK_PICTURE_RATIO = 0.98
BLACK_PIXEL_THRESHOLD = 0.10
SILENCE_DB = -45          # -45 dBFS is room tone, not speech
MIN_DEAD_SECONDS = 0.5    # shorter than this is a cut, not dead air

# What counts as worth removing. The default is the strict one -- a stretch has to be both
# black AND silent -- because that is unambiguous rubbish. Silence alone is the right rule
# for a talking video, where the picture never goes dark but half the recording is the
# person thinking; it is a separate mode because applied to ordinary footage it would
# shred anything with a quiet passage in it.
MODE_DEAD_AIR = "Black and silent (safest)"
MODE_SILENCE = "Silence only (talking videos)"
MODE_BLACK = "Black picture only"
CUT_MODES = (MODE_DEAD_AIR, MODE_SILENCE, MODE_BLACK)

# Speech does not start the instant the level crosses a threshold. Cutting flush to the
# detected boundary clips the attack off the first word and the tail off the last, which
# is the single most recognisable sign of a badly auto-edited video.
SPEECH_PADDING = 0.12

# Fade applied at both ends of every kept audio segment, in seconds. Long enough to kill
# the step at a join, short enough to be inaudible on speech.
JOIN_FADE = 0.008

_BLACK_LINE = re.compile(r"black_start:(?P<start>[\d.]+)\s+black_end:(?P<end>[\d.]+)")
_SILENCE_START = re.compile(r"silence_start:\s*(?P<start>-?[\d.]+)")
_SILENCE_END = re.compile(r"silence_end:\s*(?P<end>[\d.]+)")


@dataclass
class Segment:
    """A span of the source, in seconds."""
    start: float
    end: float

    @property
    def length(self) -> float:
        return max(0.0, self.end - self.start)

    def label(self) -> str:
        return (f"{ffmpeg_tools.format_timecode(self.start)} - "
                f"{ffmpeg_tools.format_timecode(self.end)}  ({self.length:.1f}s)")


def _run_scan(locator: FfmpegLocator, source: Path, filters: list[str],
              audio_filters: list[str]) -> str:
    """One decode pass, every detector attached. Returns ffmpeg's log."""
    locator.require()
    argv = [locator.ffmpeg, "-hide_banner", "-nostats", "-i", str(source)]
    if filters:
        argv += ["-vf", ",".join(filters)]
    if audio_filters:
        argv += ["-af", ",".join(audio_filters)]
    argv += ["-f", "null", "-"]
    result = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", creationflags=_NO_WINDOW)
    return result.stderr or ""


def parse_black(log: str) -> list[Segment]:
    """blackdetect prints one complete line per interval, including the one at EOF."""
    return [Segment(float(match.group("start")), float(match.group("end")))
            for match in _BLACK_LINE.finditer(log)]


def parse_silence(log: str, duration: float) -> list[Segment]:
    """silencedetect prints start and end on separate lines, so they must be paired.

    A run that is still silent when the file ends gets a start with no end; it is closed
    at the duration rather than dropped, because trailing silence is the single most
    common piece of dead air there is.
    """
    segments: list[Segment] = []
    start: float | None = None
    for line in log.splitlines():
        opened = _SILENCE_START.search(line)
        closed = _SILENCE_END.search(line)
        if opened:
            start = max(0.0, float(opened.group("start")))
        elif closed and start is not None:
            segments.append(Segment(start, float(closed.group("end"))))
            start = None
    if start is not None and duration > start:
        segments.append(Segment(start, duration))
    return segments


def overlap(first: list[Segment], second: list[Segment]) -> list[Segment]:
    """Spans covered by both lists. Two sorted walks, no quadratic scan."""
    result: list[Segment] = []
    left = sorted(first, key=lambda s: s.start)
    right = sorted(second, key=lambda s: s.start)
    i = j = 0
    while i < len(left) and j < len(right):
        start = max(left[i].start, right[j].start)
        end = min(left[i].end, right[j].end)
        if end > start:
            result.append(Segment(start, end))
        if left[i].end < right[j].end:
            i += 1
        else:
            j += 1
    return result


def merge(segments: list[Segment], gap: float = 0.25) -> list[Segment]:
    """Joins spans separated by less than `gap` -- a single stray frame in the middle of
    three seconds of black should not split it into two cuts."""
    ordered = sorted(segments, key=lambda s: s.start)
    merged: list[Segment] = []
    for segment in ordered:
        if merged and segment.start - merged[-1].end <= gap:
            merged[-1] = Segment(merged[-1].start, max(merged[-1].end, segment.end))
        else:
            merged.append(Segment(segment.start, segment.end))
    return merged


def keep_from_cuts(cuts: list[Segment], duration: float,
                   minimum: float = 0.2) -> list[Segment]:
    """Inverts the cut list into the spans to keep."""
    keep: list[Segment] = []
    cursor = 0.0
    for cut in merge(cuts):
        if cut.start - cursor >= minimum:
            keep.append(Segment(cursor, cut.start))
        cursor = max(cursor, cut.end)
    if duration - cursor >= minimum:
        keep.append(Segment(cursor, duration))
    return keep


def shrink(segments: list[Segment], padding: float,
           minimum: float) -> list[Segment]:
    """Pulls each span in from both ends, dropping any that vanish.

    This is what stops a silence cut from clipping speech. The detector marks the moment
    the level crosses the threshold, but the attack of a word starts before that and its
    tail rings on after -- so the span that actually gets removed is the detected one
    minus a fraction of a second at each end.
    """
    kept: list[Segment] = []
    for segment in segments:
        start = segment.start + padding
        end = segment.end - padding
        if end - start >= minimum:
            kept.append(Segment(start, end))
    return kept


def choose_cuts(black: list[Segment], silent: list[Segment], mode: str, has_audio: bool,
                padding: float = 0.0, minimum: float = MIN_DEAD_SECONDS) -> list[Segment]:
    """The spans to remove, for one of the three rules.

    With no audio track, silence cannot be tested at all, so the silence rules fall back
    to the picture rather than quietly cutting nothing (or, worse, everything).
    """
    if mode == MODE_BLACK or not has_audio:
        candidates = black
        pad = 0.0        # a black frame is black to its edge; nothing to protect
    elif mode == MODE_SILENCE:
        candidates = silent
        pad = padding
    else:
        candidates = overlap(black, silent)
        pad = 0.0
    trimmed = shrink(merge(candidates), pad, minimum) if pad else merge(candidates)
    return [segment for segment in trimmed if segment.length >= minimum]


def map_time(seconds: float, cuts: list[Segment], duration: float) -> float | None:
    """Where a moment in the source lands in the repaired file, or None if it was cut.

    A before/after comparison is worthless without this. Cutting eight seconds of dead air
    shifts everything after it, so showing source t=12s next to output t=12s puts two
    unrelated frames side by side and makes a good repair look like a broken one.
    """
    offset = 0.0
    for segment in keep_from_cuts(cuts, duration):
        if segment.start <= seconds < segment.end:
            return offset + (seconds - segment.start)
        offset += segment.length
    return None


def unmap_time(output_seconds: float, cuts: list[Segment], duration: float) -> float:
    """The inverse: a moment in the repaired file, back in the source's own clock."""
    remaining = max(0.0, output_seconds)
    for segment in keep_from_cuts(cuts, duration):
        if remaining < segment.length:
            return segment.start + remaining
        remaining -= segment.length
    return duration


# ======================================================================================
# Measurement
# ======================================================================================

# Immerkaer's estimator: convolve with the Laplacian-of-Laplacian mask and take the mean
# absolute response. Edges contribute far less to it than they do to a plain Laplacian,
# which is why it reads noise rather than detail.
_IMMERKAER_SCALE = math.sqrt(math.pi / 2) / 6

# The Laplacian of white noise has variance sigma^2 * sum(kernel^2) = 20 * sigma^2, so
# that much of the measured detail is the noise itself and has to come back off.
_LAPLACIAN_ENERGY = 20.0

SAMPLE_CROP = 512      # a centre crop, never a scale -- see _grab_gray


@dataclass
class Measurement:
    noise: float = 0.0          # estimated sigma, 0-255 scale
    sharpness: float = 0.0      # Laplacian variance with the noise contribution removed
    brightness: float = 0.0     # mean luma, 0-255
    samples: int = 0

    @property
    def noise_word(self) -> str:
        if self.noise < 1.5:
            return "clean"
        if self.noise < 3.0:
            return "slightly noisy"
        if self.noise < 6.0:
            return "noisy"
        if self.noise < 10.0:
            return "very noisy"
        return "extremely noisy"

    @property
    def detail_word(self) -> str:
        if self.sharpness < 15:
            return "very soft"
        if self.sharpness < 80:
            return "soft"
        return "sharp"


def _grab_gray(locator: FfmpegLocator, source: Path, at: float,
               width: int, height: int) -> "object | None":
    """One frame as raw 8-bit grey, at native resolution.

    Cropping rather than scaling is not a detail: any resampling averages neighbouring
    pixels, which is itself a denoise, and the measured sigma collapses. The centre crop
    keeps the real pixel statistics.
    """
    try:
        import numpy as np
    except ImportError:
        return None

    crop_width = min(SAMPLE_CROP, width) or SAMPLE_CROP
    crop_height = min(SAMPLE_CROP, height) or SAMPLE_CROP
    result = subprocess.run(
        [locator.ffmpeg, "-v", "error", "-ss", f"{at:.3f}", "-i", str(source),
         "-frames:v", "1", "-vf", f"crop={crop_width}:{crop_height},format=gray",
         "-f", "rawvideo", "-"],
        capture_output=True, creationflags=_NO_WINDOW,
    )
    expected = crop_width * crop_height
    if result.returncode != 0 or len(result.stdout) < expected:
        return None
    return np.frombuffer(result.stdout[:expected], dtype=np.uint8) \
             .reshape(crop_height, crop_width).astype(np.float32)


def _frame_stats(frame) -> tuple[float, float, float]:
    import numpy as np

    a = frame
    corners = a[:-2, :-2] + a[:-2, 2:] + a[2:, :-2] + a[2:, 2:]
    edges = a[:-2, 1:-1] + a[2:, 1:-1] + a[1:-1, :-2] + a[1:-1, 2:]
    immerkaer = corners - 2 * edges + 4 * a[1:-1, 1:-1]
    noise = float(_IMMERKAER_SCALE * np.abs(immerkaer).sum() / immerkaer.size)

    laplacian = edges - 4 * a[1:-1, 1:-1]
    detail = float(np.var(laplacian))
    return noise, max(0.0, detail - _LAPLACIAN_ENERGY * noise * noise), float(a.mean())


def sample_times(duration: float, skip: list[Segment], count: int = 6) -> list[float]:
    """Evenly spaced sample points that avoid the dead spans.

    Sampling a black intro reports a perfectly clean, perfectly blurry video, so the
    measurement has to be taken from the footage that survives the cut.
    """
    live = keep_from_cuts(skip, duration, minimum=0.4) or [Segment(0.0, duration)]
    total = sum(segment.length for segment in live)
    if total <= 0:
        return [duration / 2]
    step = total / (count + 1)
    times: list[float] = []
    for index in range(1, count + 1):
        offset = step * index
        for segment in live:
            if offset <= segment.length:
                times.append(segment.start + offset)
                break
            offset -= segment.length
    return times


def measure(locator: FfmpegLocator, source: Path, duration: float, width: int, height: int,
            skip: list[Segment] | None = None, count: int = 6,
            progress: Progress = _noop) -> Measurement:
    frames = []
    times = sample_times(duration, skip or [], count)
    for index, at in enumerate(times):
        progress(index / max(1, len(times)), f"Measuring frame at {at:.1f}s")
        frame = _grab_gray(locator, source, at, width, height)
        if frame is not None:
            frames.append(frame)
    if not frames:
        return Measurement()

    stats = [_frame_stats(frame) for frame in frames]
    # The median, not the mean: one flash frame or one out-of-focus frame should not
    # decide the setting for the whole file.
    def median(values: list[float]) -> float:
        ordered = sorted(values)
        middle = len(ordered) // 2
        if len(ordered) % 2:
            return ordered[middle]
        return (ordered[middle - 1] + ordered[middle]) / 2

    return Measurement(
        noise=median([s[0] for s in stats]),
        sharpness=median([s[1] for s in stats]),
        brightness=median([s[2] for s in stats]),
        samples=len(frames),
    )


# ======================================================================================
# The report
# ======================================================================================

@dataclass
class Report:
    source: Path
    info: MediaInfo
    black: list[Segment] = field(default_factory=list)
    silent: list[Segment] = field(default_factory=list)
    dead: list[Segment] = field(default_factory=list)
    measurement: Measurement = field(default_factory=Measurement)
    bits_per_pixel: float = 0.0
    notes: list[str] = field(default_factory=list)
    mode: str = MODE_DEAD_AIR
    padding: float = 0.0
    min_dead: float = MIN_DEAD_SECONDS
    crop: diagnose.CropBox = field(default_factory=diagnose.CropBox)
    shake: diagnose.ShakeReading = field(default_factory=diagnose.ShakeReading)
    interlace: diagnose.InterlaceReading = field(default_factory=diagnose.InterlaceReading)
    audio: diagnose.AudioReading = field(default_factory=diagnose.AudioReading)
    loudness_target: float = diagnose.LOUDNESS_TARGETS[diagnose.DEFAULT_LOUDNESS]

    def recompute(self, mode: str, padding: float = SPEECH_PADDING,
                  minimum: float | None = None) -> None:
        """Re-derives the cut list for a different rule, with no second decode.

        The black and silent spans are already stored, so switching between "black and
        silent" and "silence only" is arithmetic. Re-scanning the file to answer a
        dropdown change would take as long as the original analysis for no new
        information.
        """
        self.mode = mode
        self.padding = padding
        self.min_dead = self.min_dead if minimum is None else minimum
        self.dead = choose_cuts(self.black, self.silent, mode, self.info.has_audio,
                                padding, self.min_dead)

    @property
    def dead_seconds(self) -> float:
        return sum(segment.length for segment in self.dead)

    @property
    def dead_share(self) -> float:
        return self.dead_seconds / self.info.duration if self.info.duration else 0.0

    def frames_after_cuts(self, cuts: list[Segment] | None = None) -> int:
        """Frames the AI stage would actually have to process.

        Counted after the cuts, because there is no sense quoting -- or spending -- three
        minutes of GPU time on footage that is about to be deleted.
        """
        removed = sum(segment.length for segment in (self.dead if cuts is None else cuts))
        seconds = max(0.0, self.info.duration - removed)
        return max(1, int(round(seconds * (self.info.fps or 25.0))))

    def findings(self) -> list[str]:
        """Plain sentences, in the order a person would want to read them."""
        lines: list[str] = []
        if self.dead and self.mode == MODE_SILENCE and self.info.has_audio:
            lines.append(
                f"{len(self.dead)} silent gap(s) totalling {self.dead_seconds:.1f}s, "
                f"{self.dead_share * 100:.0f}% of the video. The picture is not checked "
                f"in this mode, so anything quiet goes — including a deliberate pause."
            )
        elif self.dead and self.mode == MODE_BLACK:
            lines.append(
                f"{len(self.dead)} black section(s) totalling {self.dead_seconds:.1f}s, "
                f"{self.dead_share * 100:.0f}% of the video. Sound is not checked in this "
                f"mode."
            )
        elif self.dead and self.info.has_audio:
            lines.append(
                f"{len(self.dead)} dead section(s) — black and silent — totalling "
                f"{self.dead_seconds:.1f}s, {self.dead_share * 100:.0f}% of the video."
            )
        elif self.dead:
            # Saying "black and silent" of a file with no audio track would be a claim
            # about sound that was never checked.
            lines.append(
                f"{len(self.dead)} black section(s) totalling {self.dead_seconds:.1f}s, "
                f"{self.dead_share * 100:.0f}% of the video. This file has no audio "
                f"track, so black alone decides the cut."
            )
        elif self.black:
            lines.append(f"{len(self.black)} black section(s) found, but sound continues "
                         f"through them, so nothing is cut by default.")
        elif self.silent and self.mode == MODE_DEAD_AIR:
            lines.append(
                f"No dead air found, but there is {sum(s.length for s in self.silent):.1f}s "
                f"of silence with a normal picture behind it. If this is someone talking, "
                f"switch the rule to 'Silence only'."
            )
        else:
            lines.append("Nothing worth cutting was found.")

        lines.append(
            f"Noise level {self.measurement.noise_word} (sigma {self.measurement.noise:.2f} "
            f"from {self.measurement.samples} sampled frames)."
        )
        lines.append(
            f"Detail reads {self.measurement.detail_word} "
            f"(score {self.measurement.sharpness:.0f}, noise already discounted)."
        )
        if not self.crop.is_full_frame:
            lines.append(self.crop.describe())
        if self.shake.samples:
            lines.append(self.shake.explain())
        if self.interlace.total:
            lines.append(self.interlace.explain())
        if self.audio.present:
            lines.append(self.audio.explain(self.loudness_target))
        lines.append(f"{self.info.resolution} at {self.info.fps:.0f} fps"
                     + (f", {self.bits_per_pixel:.3f} bits per pixel"
                        if self.bits_per_pixel else ""))
        lines += self.notes
        return lines


def scan(locator: FfmpegLocator, source: Path, silence_db: int = SILENCE_DB,
         min_dead: float = MIN_DEAD_SECONDS, mode: str = MODE_DEAD_AIR,
         padding: float = SPEECH_PADDING,
         loudness_target: float = diagnose.LOUDNESS_TARGETS[diagnose.DEFAULT_LOUDNESS],
         progress: Progress = _noop) -> Report:
    """One decode pass for the detectors, then a handful of frame grabs to measure."""
    locator.require()
    info = ffmpeg_tools.probe(locator, source)
    if info.kind != "video":
        raise RuntimeError(f"{source.name} is {info.kind}, not a video.")
    # `kind` comes from the file extension, so anything named .mp4 arrives here claiming
    # to be video. A file with no decodable stream would otherwise sail through and be
    # reported as a perfectly clean, perfectly blurry zero-second clip -- which then gets
    # "repaired" into a broken output. The probe's own numbers are the check.
    if info.duration <= 0 or info.width <= 0 or info.height <= 0:
        raise RuntimeError(
            f"ffmpeg could not read a video stream from {source.name}. The file may be "
            f"corrupt, still being written, or not really a video despite its name."
        )

    # Every detector rides along on ONE decode. Running them separately would mean six
    # passes over the file to learn things that all fall out of the same frames.
    progress(0.05, "Measuring the picture and the sound")
    video_filters = [
        f"blackdetect=d={min_dead}:pic_th={BLACK_PICTURE_RATIO}:pix_th={BLACK_PIXEL_THRESHOLD}",
        diagnose.CROP_FILTER,
        "idet",
    ]
    audio_filters = (
        [f"silencedetect=noise={silence_db}dB:d={min_dead}", diagnose.AUDIO_FILTERS]
        if info.has_audio else []
    )
    log = _run_scan(locator, source, video_filters, audio_filters)
    black = merge(parse_black(log))
    silent = merge(parse_silence(log, info.duration)) if info.has_audio else []
    crop = diagnose.parse_crop(log, info.width, info.height)
    interlace = diagnose.parse_interlace(log)
    audio = diagnose.parse_audio(log, info.has_audio)

    dead = choose_cuts(black, silent, mode, info.has_audio, padding, min_dead)

    progress(0.45, "Measuring noise and detail")
    measurement = measure(locator, source, info.duration, info.width, info.height, skip=dead,
                          progress=lambda f, m: progress(0.45 + 0.3 * f, m))

    progress(0.75, "Measuring camera movement")
    shake = diagnose.measure_shake(locator, source, info.duration, skip=dead)

    pixels = info.width * info.height * (info.fps or 25.0)
    bits_per_pixel = (info.size_bytes * 8 / info.duration / pixels
                      if pixels and info.duration else 0.0)

    notes: list[str] = []
    if measurement.samples == 0:
        notes.append("Could not read any frames for measurement — numpy may be missing, "
                     "so the noise and detail settings below are defaults, not readings.")
    if bits_per_pixel and bits_per_pixel < 0.04:
        notes.append(f"Heavily compressed ({bits_per_pixel:.3f} bpp) — expect blocking and "
                     f"banding in gradients; deband is switched on for this.")
    if info.has_audio and black and not dead:
        notes.append("Black frames carry sound here, so they are probably an intentional "
                     "fade rather than dead air.")

    progress(1.0, "Scan finished")
    return Report(source=source, info=info, black=black, silent=silent, dead=dead,
                  measurement=measurement, bits_per_pixel=bits_per_pixel, notes=notes,
                  mode=mode, padding=padding, min_dead=min_dead,
                  crop=crop, shake=shake, interlace=interlace, audio=audio,
                  loudness_target=loudness_target)


# ======================================================================================
# The plan
# ======================================================================================

DENOISE_NONE = "None"
DENOISE_FAST = "hqdn3d (fast, general)"
DENOISE_BEST = "nlmeans (slow, best quality)"
DENOISE_TEMPORAL = "atadenoise (temporal, low grain)"

# Real-ESRGAN only ships x2, x3 and x4 models.
AI_SCALES = (2, 3, 4)
UPSCALE_TOOLS = ("realesrgan", "realcugan", "waifu2x")


@dataclass
class Plan:
    cut_dead: bool = True
    cuts: list[Segment] = field(default_factory=list)
    deinterlace: bool = False
    crop: diagnose.CropBox | None = None
    stabilise: bool = False
    stabilise_smoothing: int = 10
    normalise_audio: bool = False
    loudness_target: float = -14.0
    denoise: str = DENOISE_NONE
    denoise_strength: float = 3.0
    deband: bool = False
    sharpen: float = 0.0
    ai_upscale: bool = False
    ai_suggested: bool = False   # worth doing, but not switched on for the user
    ai_tool: str = "realesrgan"
    ai_model: str = ""          # empty means the engine's own default
    ai_gpu: int | None = None   # None means "pick the fastest device"
    ai_scale: int = 2
    crf: int = 18
    reasons: list[str] = field(default_factory=list)

    @property
    def does_anything(self) -> bool:
        return bool((self.cut_dead and self.cuts) or self.denoise != DENOISE_NONE
                    or self.deband or self.sharpen > 0 or self.ai_upscale
                    or self.deinterlace or self.stabilise or self.normalise_audio
                    or (self.crop is not None and not self.crop.is_full_frame))

    def describe(self) -> list[str]:
        steps: list[str] = []
        if self.cut_dead and self.cuts:
            total = sum(segment.length for segment in self.cuts)
            steps.append(f"Cut {len(self.cuts)} dead section(s), removing {total:.1f}s")
        if self.deinterlace:
            steps.append("Deinterlace")
        if self.crop is not None and not self.crop.is_full_frame:
            steps.append(f"Crop the black bars away "
                         f"({self.crop.width}x{self.crop.height})")
        if self.normalise_audio:
            steps.append(f"Set the loudness to {self.loudness_target:.0f} LUFS")
        if self.stabilise:
            steps.append(f"Stabilise the camera (smoothing {self.stabilise_smoothing})")
        if self.denoise != DENOISE_NONE:
            steps.append(f"Denoise with {self.denoise.split(' ')[0]} at "
                         f"strength {self.denoise_strength:g}")
        if self.deband:
            steps.append("Deband the gradients")
        if self.ai_upscale:
            from . import gpu as gpu_module
            entry = gpu_module.model(self.ai_model) if self.ai_model else None
            which = f" ({entry.label})" if entry else ""
            steps.append(f"AI upscale x{self.ai_scale} with {self.ai_tool}{which}")
        if self.sharpen > 0:
            steps.append(f"Sharpen by {self.sharpen:g}")
        return steps or ["Nothing to do — this file is already clean."]


def recommend(report: Report, upscaler_installed: bool = False,
              tool_id: str = "realesrgan") -> Plan:
    """Turns the measurements into settings, and records why each one was chosen."""
    from . import gpu as gpu_module

    plan = Plan(ai_tool=tool_id, ai_model=gpu_module.DEFAULT_MODEL)
    reasons: list[str] = []

    plan.cuts = list(report.dead)
    plan.cut_dead = bool(report.dead)
    if report.dead and report.mode == MODE_SILENCE:
        reasons.append(
            f"Cutting {report.dead_seconds:.1f}s of silence across {len(report.dead)} "
            f"gap(s), keeping {report.padding * 1000:.0f}ms either side so words are not "
            f"clipped."
        )
    elif report.dead and report.mode == MODE_BLACK:
        reasons.append(f"Cutting {report.dead_seconds:.1f}s of black picture.")
    elif report.dead:
        reasons.append(f"Cutting {report.dead_seconds:.1f}s that is black and silent.")

    noise = report.measurement.noise
    if report.measurement.samples == 0:
        plan.denoise, plan.denoise_strength = DENOISE_FAST, 2.0
        reasons.append("Nothing could be measured, so a mild general denoise is used.")
    elif noise < 1.5:
        plan.denoise = DENOISE_NONE
        reasons.append(f"Noise sigma {noise:.2f} is below the level worth touching — "
                       f"denoising a clean source only smears detail.")
    elif noise < 3.0:
        plan.denoise, plan.denoise_strength = DENOISE_FAST, 1.5
        reasons.append(f"Light grain (sigma {noise:.2f}) — a gentle hqdn3d pass.")
    elif noise < 6.0:
        plan.denoise, plan.denoise_strength = DENOISE_FAST, 3.0
        reasons.append(f"Clear noise (sigma {noise:.2f}) — hqdn3d at normal strength.")
    elif noise < 10.0:
        plan.denoise, plan.denoise_strength = DENOISE_FAST, 5.0
        reasons.append(f"Heavy noise (sigma {noise:.2f}) — hqdn3d pushed hard.")
    else:
        plan.denoise, plan.denoise_strength = DENOISE_BEST, min(12.0, noise)
        reasons.append(f"Severe noise (sigma {noise:.2f}) — nlmeans, which is slow but the "
                       f"only thing that survives this much grain.")

    # -- interlacing: first, because everything else assumes whole frames -------------
    if report.interlace.interlaced:
        plan.deinterlace = True
        reasons.append(f"Interlaced ({report.interlace.field_order}) — deinterlacing "
                       f"first, or every later step works on combed frames.")
    elif report.interlace.total and max(report.interlace.tff, report.interlace.bff)             > report.interlace.progressive:
        reasons.append("Not deinterlacing: the field-order counts are mixed, which is "
                       "what fine detail does to the detector, not what interlacing "
                       "looks like.")

    # -- black bars ---------------------------------------------------------------------
    if not report.crop.is_full_frame and report.crop.share_removed > 0.02:
        plan.crop = report.crop
        reasons.append(f"{report.crop.describe()} Cropping them off means the bitrate is "
                       f"spent on picture instead of on black.")

    # -- camera shake ---------------------------------------------------------------------
    if report.shake.worth_stabilising:
        plan.stabilise = True
        reasons.append(f"{report.shake.explain()} Stabilising costs a second analysis "
                       f"pass and crops in slightly to hide the edges.")
    elif report.shake.samples and report.shake.drift > 8:
        reasons.append(report.shake.explain())

    # -- loudness --------------------------------------------------------------------------
    if report.audio.present:
        plan.loudness_target = report.loudness_target
        offset = report.audio.offset_from(report.loudness_target)
        if abs(offset) >= 2.0:
            plan.normalise_audio = True
            reasons.append(
                f"Loudness is {report.audio.lufs:.1f} LUFS, {abs(offset):.1f} dB "
                f"{'below' if offset > 0 else 'above'} the "
                f"{report.loudness_target:.0f} LUFS target — normalising it."
            )
        else:
            reasons.append(f"Loudness is already within {abs(offset):.1f} dB of target; "
                           f"left alone.")
        if report.audio.is_clipping:
            reasons.append(
                f"{report.audio.clipping_share * 100:.2f}% of samples are at full scale. "
                f"The distortion is baked in and no filter can remove it — the level is "
                f"corrected so it stops getting worse, but the peaks stay flat."
            )

    plan.deband = bool(report.bits_per_pixel and report.bits_per_pixel < 0.04)
    if plan.deband:
        reasons.append("Low bitrate for this resolution, so banding is likely.")

    short_side = min(report.info.width, report.info.height) or 1080
    plan.ai_scale = 4 if short_side < 500 else 2
    if short_side < 900 and upscaler_installed:
        # Recommended, but deliberately left switched OFF. Every other setting here costs
        # one re-encode; this one rebuilds every frame on the GPU and runs for minutes or
        # hours. Ticking it on the user's behalf would be the one place in this panel
        # where pressing the obvious button commits to something they did not choose.
        plan.ai_upscale = False
        plan.ai_suggested = True
        reasons.append(f"The short side is only {short_side}px, so an AI upscale x"
                       f"{plan.ai_scale} would add real detail rather than just pixels. "
                       f"It is left switched off because it rebuilds every frame — tick "
                       f"it, then press 'Time it on my machine' to see what that costs.")
    elif short_side < 900:
        reasons.append(f"The short side is only {short_side}px. An AI upscale would help, "
                       f"but no upscaler is installed — install one from the Model Hub "
                       f"(free, offline, one download).")

    if plan.ai_upscale or plan.ai_suggested:
        # Real-ESRGAN reconstructs edges; unsharp on top of it produces halos.
        plan.sharpen = 0.0
        reasons.append("No sharpening: an AI upscale already rebuilds edges, and adding "
                       "unsharp on top of it produces halos.")
    else:
        detail = report.measurement.sharpness
        if report.measurement.samples == 0:
            plan.sharpen = 0.0
        elif detail < 15:
            plan.sharpen = 1.2
            reasons.append(f"Very soft picture (detail {detail:.0f}) — a firm unsharp pass.")
        elif detail < 80:
            plan.sharpen = 0.6
            reasons.append(f"Slightly soft (detail {detail:.0f}) — a light unsharp pass.")
        else:
            reasons.append(f"Detail is already good ({detail:.0f}); sharpening would only "
                           f"add halos.")

    plan.reasons = reasons
    return plan


# ======================================================================================
# Execution
# ======================================================================================

def build_filters(plan: Plan) -> list[str]:
    """Video filters in the order that does the least damage.

    Denoise first: every later stage amplifies whatever noise is left, and sharpening
    noise is how a repair ends up looking worse than the original.
    """
    chain: list[str] = []
    # Deinterlace first. It reads pairs of fields, so anything that resamples, crops or
    # blurs beforehand has already destroyed what it needs.
    if plan.deinterlace:
        chain.append("yadif=mode=0:parity=-1:deint=0")
    # Then crop, so every later filter works on picture rather than on black bars -- and
    # a denoise pass is not spending time on them.
    if plan.crop is not None and not plan.crop.is_full_frame:
        chain.append(plan.crop.filter_string())
    if plan.denoise == DENOISE_FAST:
        s = plan.denoise_strength
        chain.append(f"hqdn3d={s:g}:{s * 0.75:g}:{s * 2:g}:{s * 1.5:g}")
    elif plan.denoise == DENOISE_BEST:
        chain.append(f"nlmeans=s={plan.denoise_strength:g}")
    elif plan.denoise == DENOISE_TEMPORAL:
        # atadenoise averages across time, so it takes a threshold rather than a radius;
        # 0.02..0.06 is the useful span and the shared strength slider maps onto it.
        threshold = min(0.30, max(0.01, plan.denoise_strength / 100.0))
        chain.append(f"atadenoise=0a={threshold:.3f}:1a={threshold:.3f}:2a={threshold:.3f}")
    elif plan.denoise != DENOISE_NONE:
        raise RuntimeError(f"'{plan.denoise}' is not a denoise method this build knows.")
    if plan.deband:
        chain.append("deband=1thr=0.02:2thr=0.02:3thr=0.02:4thr=0.02:range=16")
    if plan.sharpen > 0:
        amount = plan.sharpen
        chain.append(f"unsharp=5:5:{amount:g}:5:5:{amount * 0.5:g}")
    return chain


def build_command(locator: FfmpegLocator, source: Path, output: Path, plan: Plan,
                  info: MediaInfo, encoder: str = "libx264") -> FfmpegCommand:
    """Cutting and cleaning in a single encode.

    Doing it as two passes would mean encoding the whole file twice and throwing away
    quality in between for no gain -- trim and the cleanup filters live happily in one
    filtergraph.
    """
    locator.require()
    cuts = list(plan.cuts) if plan.cut_dead else []
    ranges = keep_from_cuts(cuts, info.duration) if cuts else []
    filters = build_filters(plan)
    audio = info.has_audio
    # loudnorm is a single-pass dynamic normaliser here. Two-pass is more precise, but it
    # needs the measurement from a first run and this file has already been measured --
    # the target is what matters, and the difference is a fraction of a dB.
    audio_chain = (f"loudnorm=I={plan.loudness_target:g}:TP=-1.5:LRA=11"
                   if plan.normalise_audio and audio else "")

    argv = [locator.ffmpeg, "-y", "-hide_banner", "-i", str(source)]

    if ranges:
        parts: list[str] = []
        for index, segment in enumerate(ranges):
            parts.append(f"[0:v]trim=start={segment.start:.3f}:end={segment.end:.3f},"
                         f"setpts=PTS-STARTPTS[v{index}]")
            if audio:
                # Cutting audio at an arbitrary sample leaves a step in the waveform, and
                # every join then clicks. A few milliseconds of fade at each end costs
                # nothing audible and removes it. This matters most in silence mode, where
                # a talking video can end up with dozens of joins rather than two.
                fade_out = max(0.0, segment.length - JOIN_FADE)
                parts.append(
                    f"[0:a]atrim=start={segment.start:.3f}:end={segment.end:.3f},"
                    f"asetpts=PTS-STARTPTS,"
                    f"afade=t=in:st=0:d={JOIN_FADE},"
                    f"afade=t=out:st={fade_out:.3f}:d={JOIN_FADE}[a{index}]"
                )
        pads = "".join(f"[v{i}][a{i}]" if audio else f"[v{i}]" for i in range(len(ranges)))
        parts.append(f"{pads}concat=n={len(ranges)}:v=1:a={1 if audio else 0}"
                     + ("[vcat][acat]" if audio else "[vcat]"))
        parts.append(f"[vcat]{','.join(filters)}[vout]" if filters else "[vcat]null[vout]")
        if audio:
            # Normalise AFTER the joins, so the loudness is measured over what the viewer
            # will actually hear rather than over the silence that is about to go.
            parts.append(f"[acat]{audio_chain}[aout]" if audio_chain else "[acat]anull[aout]")
        argv += ["-filter_complex", ";".join(parts), "-map", "[vout]"]
        if audio:
            argv += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"]
        total = sum(segment.length for segment in ranges)
    else:
        if filters:
            argv += ["-vf", ",".join(filters)]
        if audio_chain:
            argv += ["-af", audio_chain]
        argv += ["-map", "0:v:0"]
        if audio:
            argv += ["-map", "0:a:0"]
            argv += ["-c:a", "aac", "-b:a", "192k"] if audio_chain else ["-c:a", "copy"]
        if not filters:
            # Nothing touches the picture, so the video stream is copied through
            # untouched. Re-encoding it to fix the loudness would cost a generation of
            # quality for a change that never reaches a single pixel.
            argv += ["-c:v", "copy", str(output)]
            return FfmpegCommand(argv, output, info.duration, "Repair (audio only)")
        total = info.duration

    argv += ["-c:v", encoder, "-crf", str(plan.crf), "-preset", "medium",
             "-pix_fmt", "yuv420p", str(output)]
    return FfmpegCommand(argv, output, total, "Repair")


def _escape_filter_path(path: str) -> str:
    """ffmpeg filter arguments need forward slashes and an escaped drive colon."""
    return path.replace(chr(92), "/").replace(":", chr(92) + ":")


def _stabilise(locator: FfmpegLocator, source: Path, output: Path, plan: Plan,
               progress: Progress, cancel: Cancel | None) -> Path:
    """vidstab, both passes.

    Pass one only measures -- it writes a transform file and no video. Pass two applies
    the smoothed path, zooming in slightly so the edges it swings past are never visible.
    That zoom is why stabilisation is not free: a little of the frame is genuinely gone.
    """
    transforms = output.with_name(f".{output.stem}.trf")
    try:
        detect = ffmpeg_tools.FfmpegCommand(
            [locator.ffmpeg, "-y", "-hide_banner", "-i", str(source),
             # The drive letter's colon is the filter parser's option separator, so an
             # unescaped Windows path makes ffmpeg reject the whole filterchain.
             "-vf", f"vidstabdetect=shakiness=8:accuracy=15:"
                    f"result='{_escape_filter_path(str(transforms))}'",
             "-f", "null", "-"],
            output, 0.0, "Analysing camera movement",
        )
        code, log = ffmpeg_tools.run_with_progress(
            detect, on_progress=lambda f, m: progress(f * 0.45, f"Measuring shake {m}"),
            should_cancel=cancel,
        )
        if code == 130:
            raise RuntimeError("Cancelled")
        if code != 0 or not transforms.exists():
            raise RuntimeError(f"Shake analysis failed (exit {code}):\n{log[-700:]}")

        apply_command = ffmpeg_tools.FfmpegCommand(
            [locator.ffmpeg, "-y", "-hide_banner", "-i", str(source),
             "-vf", f"vidstabtransform=input='{_escape_filter_path(str(transforms))}':"
                    f"smoothing={plan.stabilise_smoothing}:crop=black:zoom=0:optzoom=1,"
                    f"unsharp=5:5:0.4:3:3:0.2",
             "-c:v", "libx264", "-crf", str(plan.crf), "-preset", "medium",
             "-pix_fmt", "yuv420p", "-c:a", "copy", str(output)],
            output, 0.0, "Stabilising",
        )
        code, log = ffmpeg_tools.run_with_progress(
            apply_command,
            on_progress=lambda f, m: progress(0.45 + f * 0.55, f"Stabilising {m}"),
            should_cancel=cancel,
        )
        if code == 130:
            raise RuntimeError("Cancelled")
        if code != 0 or not output.exists():
            raise RuntimeError(f"Stabilising failed (exit {code}):\n{log[-700:]}")
        return output
    finally:
        transforms.unlink(missing_ok=True)


def repair(locator: FfmpegLocator, source: Path, output: Path, plan: Plan,
           info: MediaInfo | None = None, local_tools=None,
           progress: Progress = _noop, cancel: Cancel | None = None) -> Path:
    """Runs the plan. Returns the finished file; the source is never touched.

    Three stages, because two of them cannot share a filtergraph: stabilisation has to
    measure the whole clip before it can transform a single frame, and the AI upscaler
    only speaks directories of stills. Everything else -- cutting, deinterlacing,
    cropping, denoising, sharpening and the loudness -- fits in one encode.
    """
    from . import localai

    locator.require()
    info = info or ffmpeg_tools.probe(locator, source)
    output.parent.mkdir(parents=True, exist_ok=True)

    if plan.cut_dead and plan.cuts and not keep_from_cuts(plan.cuts, info.duration):
        raise RuntimeError(
            "Those cuts would remove the whole video — every part of it reads as black "
            "and silent. Loosen the thresholds or untick some sections."
        )

    # Each stage's share of the progress bar is sized by roughly how long it takes, so
    # the bar does not sit at 90% for an hour while the upscaler works.
    weights = [1.0]
    if plan.stabilise:
        weights.append(1.5)
    if plan.ai_upscale:
        weights.append(6.0)
    stages = len(weights)
    total_weight = sum(weights)
    bounds, running = [], 0.0
    for weight in weights:
        bounds.append((running / total_weight, (running + weight) / total_weight))
        running += weight

    staging: list[Path] = []

    def stage_path(index: int) -> Path:
        if index == stages - 1:
            return output
        candidate = output.with_name(f".{output.stem}_stage{index + 1}.mp4")
        staging.append(candidate)
        return candidate

    def band(index: int):
        low, high = bounds[index]
        return lambda fraction, message: progress(low + (high - low) * fraction, message)

    try:
        stage = 0
        # -- stage 1: cut, deinterlace, crop, denoise, deband, sharpen, loudness ------
        current = stage_path(stage)
        command = build_command(locator, source, current, plan, info)
        code, log = ffmpeg_tools.run_with_progress(
            command, on_progress=band(stage), should_cancel=cancel,
        )
        if code == 130:
            raise RuntimeError("Cancelled")
        if code != 0 or not current.exists():
            raise RuntimeError(f"The cleanup pass failed (exit {code}):\n{log[-900:]}")
        stage += 1

        # -- stage 2: stabilise -------------------------------------------------------
        if plan.stabilise:
            target = stage_path(stage)
            _stabilise(locator, current, target, plan, band(stage), cancel)
            current = target
            stage += 1

        # -- stage 3: AI upscale ------------------------------------------------------
        if plan.ai_upscale:
            tool = local_tools.get(plan.ai_tool) if local_tools else None
            if tool is None or not tool.installed:
                # Everything before this point is real work; hand it over rather than
                # deleting it because the last stage could not run.
                if current != output:
                    current.replace(output)
                    staging.remove(current) if current in staging else None
                raise RuntimeError(
                    f"Cleaned up successfully, but the upscaler '{plan.ai_tool}' is not "
                    f"installed, so the AI stage was skipped. The finished-so-far file is "
                    f"at {output}. Install the engine from the Model Hub and run again."
                )
            options = {"scale": plan.ai_scale}
            if plan.ai_model:
                options["model"] = plan.ai_model
            if plan.ai_gpu is not None:
                options["gpu"] = plan.ai_gpu
            localai.upscale_video(tool, current, output, options, locator,
                                  progress=band(stage), cancel=cancel)

        progress(1.0, "Done")
        return output
    finally:
        for path in staging:
            if path != output:
                path.unlink(missing_ok=True)


def installed_upscaler(local_tools) -> str:
    """The first installed image upscaler, preferring the general-purpose one."""
    if local_tools is None:
        return ""
    for tool_id in UPSCALE_TOOLS:
        tool = local_tools.get(tool_id)
        if tool is not None and tool.installed:
            return tool_id
    return ""
