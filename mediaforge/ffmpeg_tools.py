"""FFmpeg/FFprobe wrapper: probing, thumbnails, and the local editing operations.

Every operation returns an `FfmpegCommand` describing the argv it will run, so the UI
can show the exact command and the job runner can stream progress from stderr.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator, Sequence

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

_TIME_PATTERN = re.compile(r"out_time_ms=(\d+)")
_DURATION_PATTERN = re.compile(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)")


class FfmpegMissing(RuntimeError):
    """Raised when no ffmpeg binary can be located."""


@dataclass(frozen=True)
class MediaInfo:
    path: Path
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool
    video_codec: str
    audio_codec: str
    size_bytes: int
    kind: str  # "video" | "image" | "audio"

    @property
    def resolution(self) -> str:
        return f"{self.width}x{self.height}" if self.width else "-"

    @property
    def pretty_duration(self) -> str:
        return format_timecode(self.duration)


def format_timecode(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    frames = int((seconds - int(seconds)) * 100)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{frames:02d}"


def parse_timecode(text: str) -> float:
    """Accepts '12', '1:05', '00:01:05.5'."""
    parts = text.strip().split(":")
    try:
        values = [float(part) for part in parts]
    except ValueError:
        return 0.0
    total = 0.0
    for value in values:
        total = total * 60 + value
    return total


def _well_known_locations(executable: str) -> list[Path]:
    """Where package managers drop ffmpeg on Windows.

    winget and Chocolatey both add their bin folder to the *user* PATH, but a process
    started before the install -- or a shell that never inherited WindowsApps -- will not
    see it. Looking directly saves the user a reboot.
    """
    if sys.platform != "win32":
        return [Path("/usr/local/bin") / executable, Path("/opt/homebrew/bin") / executable]

    local = Path(os.environ.get("LOCALAPPDATA", ""))
    candidates: list[Path] = [
        local / "Microsoft" / "WinGet" / "Links" / executable,
        Path(os.environ.get("ProgramData", "")) / "chocolatey" / "bin" / executable,
        Path(os.environ.get("ProgramFiles", "")) / "ffmpeg" / "bin" / executable,
    ]
    # winget unpacks Gyan builds into a versioned folder under Packages/.
    packages = local / "Microsoft" / "WinGet" / "Packages"
    if packages.is_dir():
        candidates.extend(sorted(packages.glob(f"Gyan.FFmpeg*/**/bin/{executable}"), reverse=True))
    return candidates


class FfmpegLocator:
    """Finds ffmpeg/ffprobe: explicit setting, then the bundled copy, then PATH.

    The bundled copy outranks PATH on purpose. It is the build the app was tested
    against; an older ffmpeg that happens to be on PATH can lack vidstab, drawtext or
    loudnorm, and the failure would surface as a broken render rather than a clear error.
    """

    def __init__(self, ffmpeg_hint: str = "", ffprobe_hint: str = "") -> None:
        self.ffmpeg = self._resolve("ffmpeg", ffmpeg_hint)
        self.ffprobe = self._resolve("ffprobe", ffprobe_hint)

    @staticmethod
    def _resolve(name: str, hint: str) -> str:
        from .paths import bundled_ffmpeg_dir

        executable = name + (".exe" if sys.platform == "win32" else "")

        if hint:
            candidate = Path(hint)
            if candidate.is_dir():
                candidate = candidate / executable
            if candidate.exists():
                return str(candidate)

        bundled = bundled_ffmpeg_dir() / executable
        if bundled.exists():
            return str(bundled)

        found = shutil.which(name)
        if found:
            return found

        for candidate in _well_known_locations(executable):
            if candidate.exists():
                return str(candidate)
        return ""

    @property
    def available(self) -> bool:
        return bool(self.ffmpeg and self.ffprobe)

    def require(self) -> None:
        if not self.available:
            raise FfmpegMissing(
                "ffmpeg and ffprobe were not found. Install them "
                "(winget install Gyan.FFmpeg) or set the path in Settings."
            )

    def version(self) -> str:
        if not self.ffmpeg:
            return "not found"
        result = subprocess.run(
            [self.ffmpeg, "-version"], capture_output=True, text=True, creationflags=_NO_WINDOW
        )
        return result.stdout.splitlines()[0] if result.stdout else "unknown"


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif", ".heic"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mpg", ".mpeg", ".wmv"}
AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}


def classify(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in AUDIO_SUFFIXES:
        return "audio"
    return "video"


def probe(locator: FfmpegLocator, path: Path) -> MediaInfo:
    locator.require()
    result = subprocess.run(
        [
            locator.ffprobe, "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", str(path),
        ],
        capture_output=True, text=True, creationflags=_NO_WINDOW,
    )
    payload = json.loads(result.stdout or "{}")
    streams = payload.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    fmt = payload.get("format", {})

    fps = 0.0
    rate = video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/1"
    if "/" in rate:
        numerator, _, denominator = rate.partition("/")
        try:
            fps = float(numerator) / float(denominator) if float(denominator) else 0.0
        except ValueError:
            fps = 0.0

    return MediaInfo(
        path=path,
        duration=float(fmt.get("duration") or 0.0),
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        fps=round(fps, 3),
        has_audio=bool(audio),
        video_codec=video.get("codec_name", ""),
        audio_codec=audio.get("codec_name", ""),
        size_bytes=int(fmt.get("size") or (path.stat().st_size if path.exists() else 0)),
        kind=classify(path),
    )


@dataclass
class FfmpegCommand:
    argv: list[str]
    output: Path
    total_duration: float = 0.0
    description: str = ""

    def display(self) -> str:
        return " ".join(f'"{part}"' if " " in part else part for part in self.argv)


def make_thumbnail(
    locator: FfmpegLocator, source: Path, destination: Path, at_seconds: float = 1.0, width: int = 320
) -> bool:
    """Grabs a single frame. Returns False rather than raising -- thumbnails are cosmetic."""
    if not locator.available:
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    kind = classify(source)
    argv = [locator.ffmpeg, "-y", "-v", "error"]
    if kind == "video":
        argv += ["-ss", f"{at_seconds:.3f}"]
    argv += ["-i", str(source), "-frames:v", "1", "-vf", f"scale={width}:-2", str(destination)]
    result = subprocess.run(argv, capture_output=True, creationflags=_NO_WINDOW)
    return result.returncode == 0 and destination.exists()


def run_with_progress(
    command: FfmpegCommand,
    on_progress: Callable[[float, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> tuple[int, str]:
    """Runs ffmpeg, reporting 0..1 progress parsed from `-progress pipe:1`."""
    argv = list(command.argv)
    if "-progress" not in argv:
        argv = argv[:1] + ["-progress", "pipe:1", "-nostats"] + argv[1:]

    process = subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", creationflags=_NO_WINDOW,
    )
    total = command.total_duration

    # stderr must be drained concurrently. ffmpeg writes stream and encoder detail there,
    # and once that pipe's buffer fills it blocks on write -- which stops stdout, which
    # deadlocks the progress loop below. Keep only the tail; that is all an error needs.
    tail: deque[str] = deque(maxlen=40)

    def drain_stderr() -> None:
        if process.stderr is None:
            return
        for line in process.stderr:
            tail.append(line.rstrip())

    stderr_thread = threading.Thread(target=drain_stderr, daemon=True)
    stderr_thread.start()

    cancelled = False
    assert process.stdout is not None
    for line in process.stdout:
        if should_cancel and should_cancel():
            cancelled = True
            process.terminate()
            break
        match = _TIME_PATTERN.search(line)
        if match and total > 0 and on_progress:
            seconds = int(match.group(1)) / 1_000_000
            on_progress(min(seconds / total, 0.999), format_timecode(seconds))

    process.wait()
    stderr_thread.join(timeout=5)
    if cancelled:
        return 130, "cancelled"
    if on_progress and process.returncode == 0:
        on_progress(1.0, "done")
    return process.returncode, "\n".join(tail)


# --------------------------------------------------------------------------------------
# Operation builders. Each returns an FfmpegCommand; nothing executes here.
# --------------------------------------------------------------------------------------

def _base(locator: FfmpegLocator) -> list[str]:
    locator.require()
    return [locator.ffmpeg, "-y", "-hide_banner"]


def trim(
    locator: FfmpegLocator, source: Path, output: Path, start: float, end: float, reencode: bool = True
) -> FfmpegCommand:
    duration = max(0.01, end - start)
    argv = _base(locator) + ["-ss", f"{start:.3f}", "-i", str(source), "-t", f"{duration:.3f}"]
    if reencode:
        argv += ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-c:a", "aac", "-b:a", "192k"]
    else:
        argv += ["-c", "copy"]
    argv += [str(output)]
    return FfmpegCommand(argv, output, duration, f"Trim {start:.2f}s -> {end:.2f}s")


def concat(
    locator: FfmpegLocator, sources: Sequence[Path], output: Path, width: int = 1920, height: int = 1080,
    fps: int = 30, crossfade: float = 0.0, durations: Sequence[float] | None = None,
) -> FfmpegCommand:
    """Normalises every clip to a common format, then joins -- optionally with crossfades."""
    if not sources:
        raise ValueError("concat needs at least one source")

    argv = _base(locator)
    for source in sources:
        argv += ["-i", str(source)]

    scale = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"setsar=1,fps={fps},format=yuv420p"
    )
    filters: list[str] = []
    for index in range(len(sources)):
        filters.append(f"[{index}:v]{scale}[v{index}]")

    if crossfade > 0 and len(sources) > 1 and durations:
        offset = max(0.0, durations[0] - crossfade)
        current = "v0"
        for index in range(1, len(sources)):
            label = f"x{index}"
            filters.append(
                f"[{current}][v{index}]xfade=transition=fade:duration={crossfade}:"
                f"offset={offset:.3f}[{label}]"
            )
            current = label
            offset += max(0.0, durations[index] - crossfade)
        video_out = current
        total = sum(durations) - crossfade * (len(sources) - 1)
    else:
        joined = "".join(f"[v{index}]" for index in range(len(sources)))
        filters.append(f"{joined}concat=n={len(sources)}:v=1:a=0[vout]")
        video_out = "vout"
        total = sum(durations) if durations else 0.0

    argv += [
        "-filter_complex", ";".join(filters),
        "-map", f"[{video_out}]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-an", str(output),
    ]
    return FfmpegCommand(argv, output, total, f"Join {len(sources)} clips")


def transcode(
    locator: FfmpegLocator, source: Path, output: Path, width: int = 0, height: int = 0,
    fps: int = 0, crf: int = 20, codec: str = "libx264", audio_bitrate: str = "192k",
    duration_hint: float = 0.0,
) -> FfmpegCommand:
    argv = _base(locator) + ["-i", str(source)]
    filters: list[str] = []
    if width or height:
        filters.append(f"scale={width or -2}:{height or -2}")
    if fps:
        filters.append(f"fps={fps}")
    if filters:
        argv += ["-vf", ",".join(filters)]
    argv += ["-c:v", codec, "-crf", str(crf), "-preset", "medium", "-pix_fmt", "yuv420p"]
    argv += ["-c:a", "aac", "-b:a", audio_bitrate, str(output)]
    return FfmpegCommand(argv, output, duration_hint, f"Transcode -> {output.suffix}")


def extract_audio(
    locator: FfmpegLocator, source: Path, output: Path, duration_hint: float = 0.0
) -> FfmpegCommand:
    argv = _base(locator) + ["-i", str(source), "-vn", "-c:a", "libmp3lame", "-q:a", "2", str(output)]
    return FfmpegCommand(argv, output, duration_hint, "Extract audio")


def mux_audio(
    locator: FfmpegLocator, video: Path, audio: Path, output: Path, video_duration: float = 0.0,
    music_volume: float = 1.0, keep_original: bool = False,
) -> FfmpegCommand:
    argv = _base(locator) + ["-i", str(video), "-i", str(audio)]
    if keep_original:
        argv += [
            "-filter_complex",
            f"[1:a]volume={music_volume}[bg];[0:a][bg]amix=inputs=2:duration=first:dropout_transition=2[aout]",
            "-map", "0:v", "-map", "[aout]",
        ]
    else:
        argv += ["-filter_complex", f"[1:a]volume={music_volume}[aout]", "-map", "0:v", "-map", "[aout]"]
    argv += ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", str(output)]
    return FfmpegCommand(argv, output, video_duration, "Add audio track")


def burn_subtitles(
    locator: FfmpegLocator, video: Path, subtitles: Path, output: Path, duration_hint: float = 0.0,
    font_size: int = 28, margin_v: int = 60,
) -> FfmpegCommand:
    # ffmpeg's subtitles filter needs escaped Windows paths inside the filter string.
    escaped = str(subtitles).replace("\\", "/").replace(":", "\\:")
    style = f"FontSize={font_size},MarginV={margin_v},Outline=2,Shadow=0"
    argv = _base(locator) + [
        "-i", str(video),
        "-vf", f"subtitles='{escaped}':force_style='{style}'",
        "-c:v", "libx264", "-crf", "20", "-preset", "medium", "-c:a", "copy", str(output),
    ]
    return FfmpegCommand(argv, output, duration_hint, "Burn subtitles")


def overlay_image(
    locator: FfmpegLocator, video: Path, image: Path, output: Path, position: str = "bottom-right",
    scale_percent: int = 20, opacity: float = 1.0, duration_hint: float = 0.0,
) -> FfmpegCommand:
    anchors = {
        "top-left": "10:10",
        "top-right": "W-w-10:10",
        "bottom-left": "10:H-h-10",
        "bottom-right": "W-w-10:H-h-10",
        "center": "(W-w)/2:(H-h)/2",
    }
    argv = _base(locator) + [
        "-i", str(video), "-i", str(image),
        "-filter_complex",
        f"[1:v]scale=iw*{scale_percent / 100:.3f}:-1,format=rgba,"
        f"colorchannelmixer=aa={opacity:.3f}[wm];"
        f"[0:v][wm]overlay={anchors.get(position, anchors['bottom-right'])}",
        "-c:a", "copy", "-c:v", "libx264", "-crf", "20", str(output),
    ]
    return FfmpegCommand(argv, output, duration_hint, "Overlay watermark")


def speed_change(
    locator: FfmpegLocator, source: Path, output: Path, factor: float, duration_hint: float = 0.0
) -> FfmpegCommand:
    """factor > 1 speeds up. atempo only accepts 0.5..2.0, so it gets chained."""
    factor = max(0.1, min(factor, 20.0))
    tempo_chain: list[str] = []
    remaining = factor
    while remaining > 2.0:
        tempo_chain.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        tempo_chain.append("atempo=0.5")
        remaining /= 0.5
    tempo_chain.append(f"atempo={remaining:.4f}")
    argv = _base(locator) + [
        "-i", str(source),
        "-filter_complex",
        f"[0:v]setpts={1 / factor:.6f}*PTS[v];[0:a]{','.join(tempo_chain)}[a]",
        "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-crf", "20", str(output),
    ]
    return FfmpegCommand(argv, output, duration_hint / factor, f"Speed x{factor:g}")


def color_grade(
    locator: FfmpegLocator, source: Path, output: Path, brightness: float = 0.0, contrast: float = 1.0,
    saturation: float = 1.0, gamma: float = 1.0, duration_hint: float = 0.0,
) -> FfmpegCommand:
    argv = _base(locator) + [
        "-i", str(source),
        "-vf", f"eq=brightness={brightness}:contrast={contrast}:saturation={saturation}:gamma={gamma}",
        "-c:v", "libx264", "-crf", "20", "-c:a", "copy", str(output),
    ]
    return FfmpegCommand(argv, output, duration_hint, "Color grade")


def stabilise(
    locator: FfmpegLocator, source: Path, output: Path, duration_hint: float = 0.0
) -> FfmpegCommand:
    """Single-pass deshake -- vidstab needs two passes and an optional build."""
    argv = _base(locator) + [
        "-i", str(source), "-vf", "deshake", "-c:v", "libx264", "-crf", "20", "-c:a", "copy", str(output)
    ]
    return FfmpegCommand(argv, output, duration_hint, "Stabilise")


def to_gif(
    locator: FfmpegLocator, source: Path, output: Path, fps: int = 12, width: int = 480,
    duration_hint: float = 0.0,
) -> FfmpegCommand:
    argv = _base(locator) + [
        "-i", str(source),
        "-vf", f"fps={fps},scale={width}:-1:flags=lanczos,split[a][b];"
               f"[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer",
        str(output),
    ]
    return FfmpegCommand(argv, output, duration_hint, "Export GIF")


def ken_burns(
    locator: FfmpegLocator, image: Path, output: Path, seconds: float = 5.0, fps: int = 30,
    width: int = 1920, height: int = 1080, zoom_to: float = 1.15,
) -> FfmpegCommand:
    """Turns a still into a slow-push video clip -- the workhorse for photo slideshows."""
    frames = max(1, int(seconds * fps))
    step = (zoom_to - 1.0) / frames
    argv = _base(locator) + [
        "-loop", "1", "-i", str(image), "-t", f"{seconds:.3f}",
        "-vf",
        f"scale={width * 2}:{height * 2}:force_original_aspect_ratio=increase,"
        f"crop={width * 2}:{height * 2},"
        f"zoompan=z='min(zoom+{step:.6f},{zoom_to})':d={frames}:"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={width}x{height}:fps={fps},"
        f"format=yuv420p",
        "-c:v", "libx264", "-crf", "20", "-preset", "medium", str(output),
    ]
    return FfmpegCommand(argv, output, seconds, "Ken Burns from still")


def slideshow(
    locator: FfmpegLocator, images: Sequence[Path], output: Path, seconds_each: float = 3.0,
    fps: int = 30, width: int = 1920, height: int = 1080, crossfade: float = 0.5,
) -> FfmpegCommand:
    if not images:
        raise ValueError("slideshow needs at least one image")
    argv = _base(locator)
    for image in images:
        argv += ["-loop", "1", "-t", f"{seconds_each:.3f}", "-i", str(image)]

    scale = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},format=yuv420p"
    )
    filters = [f"[{index}:v]{scale}[v{index}]" for index in range(len(images))]

    if crossfade > 0 and len(images) > 1:
        current, offset = "v0", seconds_each - crossfade
        for index in range(1, len(images)):
            label = f"x{index}"
            filters.append(
                f"[{current}][v{index}]xfade=transition=fade:duration={crossfade}:"
                f"offset={offset:.3f}[{label}]"
            )
            current, offset = label, offset + seconds_each - crossfade
        video_out = current
        total = len(images) * seconds_each - crossfade * (len(images) - 1)
    else:
        joined = "".join(f"[v{index}]" for index in range(len(images)))
        filters.append(f"{joined}concat=n={len(images)}:v=1:a=0[vout]")
        video_out, total = "vout", len(images) * seconds_each

    argv += [
        "-filter_complex", ";".join(filters), "-map", f"[{video_out}]",
        "-c:v", "libx264", "-crf", "20", "-preset", "medium", str(output),
    ]
    return FfmpegCommand(argv, output, total, f"Slideshow of {len(images)} photos")


def social_crop(
    locator: FfmpegLocator, source: Path, output: Path, aspect: str = "9:16", blur_pad: bool = True,
    duration_hint: float = 0.0,
) -> FfmpegCommand:
    """Reframes for vertical/square delivery, padding with a blurred copy instead of black bars."""
    presets = {"9:16": (1080, 1920), "1:1": (1080, 1080), "4:5": (1080, 1350), "16:9": (1920, 1080)}
    width, height = presets.get(aspect, (1080, 1920))
    if blur_pad:
        chain = (
            f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},boxblur=40:8[bg];"
            f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1"
        )
    else:
        chain = (
            f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1"
        )
    argv = _base(locator) + [
        "-i", str(source), "-filter_complex", chain,
        "-c:v", "libx264", "-crf", "20", "-c:a", "copy", str(output),
    ]
    return FfmpegCommand(argv, output, duration_hint, f"Reframe to {aspect}")


def extract_frames(
    locator: FfmpegLocator, source: Path, output_dir: Path, every_seconds: float = 1.0, width: int = 1280
) -> FfmpegCommand:
    output_dir.mkdir(parents=True, exist_ok=True)
    pattern = output_dir / "frame_%05d.jpg"
    argv = _base(locator) + [
        "-i", str(source), "-vf", f"fps=1/{every_seconds},scale={width}:-2",
        "-q:v", "2", str(pattern),
    ]
    return FfmpegCommand(argv, output_dir, 0.0, "Extract frames")


def detect_silence(locator: FfmpegLocator, source: Path, threshold_db: int = -35, min_len: float = 0.6
                   ) -> list[tuple[float, float]]:
    """Returns [(start, end)] silent ranges -- the basis for auto jump-cuts."""
    locator.require()
    result = subprocess.run(
        [locator.ffmpeg, "-i", str(source), "-af",
         f"silencedetect=noise={threshold_db}dB:d={min_len}", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=_NO_WINDOW,
    )
    ranges: list[tuple[float, float]] = []
    start: float | None = None
    for line in result.stderr.splitlines():
        if "silence_start:" in line:
            start = float(line.split("silence_start:")[1].split()[0])
        elif "silence_end:" in line and start is not None:
            ranges.append((start, float(line.split("silence_end:")[1].split()[0])))
            start = None
    return ranges


def keep_ranges(
    locator: FfmpegLocator, source: Path, output: Path, ranges: Sequence[tuple[float, float]]
) -> FfmpegCommand:
    """Keeps only the given [start, end] ranges, concatenated in order."""
    if not ranges:
        raise ValueError("keep_ranges needs at least one range")
    filters, video_labels, audio_labels = [], [], []
    for index, (start, end) in enumerate(ranges):
        filters.append(f"[0:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS[v{index}]")
        filters.append(f"[0:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS[a{index}]")
        video_labels.append(f"[v{index}]")
        audio_labels.append(f"[a{index}]")
    filters.append(
        "".join(f"{v}{a}" for v, a in zip(video_labels, audio_labels))
        + f"concat=n={len(ranges)}:v=1:a=1[vout][aout]"
    )
    total = sum(end - start for start, end in ranges)
    argv = _base(locator) + [
        "-i", str(source), "-filter_complex", ";".join(filters),
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-crf", "20", "-c:a", "aac", str(output),
    ]
    return FfmpegCommand(argv, output, total, f"Keep {len(ranges)} segments")


def invert_ranges(
    silences: Iterable[tuple[float, float]], duration: float, padding: float = 0.08
) -> list[tuple[float, float]]:
    """Turns silent ranges into the speech ranges to keep."""
    keep: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in silences:
        start = max(0.0, start + padding)
        if start - cursor > 0.15:
            keep.append((cursor, start))
        cursor = max(cursor, end - padding)
    if duration - cursor > 0.15:
        keep.append((cursor, duration))
    return keep


def write_srt(segments: Sequence[dict], destination: Path) -> Path:
    """segments: [{'start': float, 'end': float, 'text': str}]"""
    def stamp(value: float) -> str:
        hours, remainder = divmod(int(value), 3600)
        minutes, seconds = divmod(remainder, 60)
        millis = int((value - int(value)) * 1000)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"

    lines: list[str] = []
    for index, segment in enumerate(segments, start=1):
        lines += [
            str(index),
            f"{stamp(segment['start'])} --> {stamp(segment['end'])}",
            segment["text"].strip(),
            "",
        ]
    destination.write_text("\n".join(lines), encoding="utf-8")
    return destination
