"""Extra measurements: black bars, camera shake, interlacing and audio level.

Same principle as the noise estimator next door -- every recommendation the app makes has
to come from a number taken off the user's actual file. Four more things are worth
measuring, and each of them has a trap in it:

  **Black bars.** cropdetect is reliable, but it reports the crop for the frames it saw.
  Sampling only the opening of a film that fades in from black gives a crop of nothing.

  **Shake.** ffmpeg's vidstabdetect writes a binary .trf in this build, so its numbers
  cannot be read back. Shake is measured here by phase correlation between consecutive
  frames instead -- and the metric is the *change* in the shift vector, not the shift
  itself, because a smooth pan moves the whole frame every bit as much as a shaky hand
  does. Measured on real clips: a deliberate pan reads 2.4, a shaky camera 41.2.

  **Interlacing.** idet on detailed progressive footage reports a mix of TFF and BFF and
  looks superficially like interlace. Real interlaced material is overwhelmingly ONE
  field order; the test is dominance, not the raw count.

  **Audio level.** Loudness (LUFS) and clipping are different faults with different
  fixes, and a quiet recording and a clipped one both "sound wrong" to a user.
"""

from __future__ import annotations

import math
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .ffmpeg_tools import FfmpegLocator

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

Progress = Callable[[float, str], None]


def _noop(fraction: float, message: str) -> None:
    return None


def _run(locator: FfmpegLocator, argv: list[str]) -> str:
    result = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", creationflags=_NO_WINDOW)
    return (result.stderr or "") + (result.stdout or "")


# ======================================================================================
# Black bars
# ======================================================================================

_CROP_LINE = re.compile(r"crop=(\d+):(\d+):(\d+):(\d+)")


@dataclass
class CropBox:
    width: int = 0
    height: int = 0
    x: int = 0
    y: int = 0
    source_width: int = 0
    source_height: int = 0

    @property
    def valid(self) -> bool:
        return self.width > 0 and self.height > 0

    @property
    def is_full_frame(self) -> bool:
        return (not self.valid
                or (self.width >= self.source_width and self.height >= self.source_height))

    @property
    def removed_pixels(self) -> int:
        if not self.valid:
            return 0
        return (self.source_width * self.source_height) - (self.width * self.height)

    @property
    def share_removed(self) -> float:
        total = self.source_width * self.source_height
        return self.removed_pixels / total if total else 0.0

    def describe(self) -> str:
        if self.is_full_frame:
            return "No black bars."
        top = self.y
        bottom = self.source_height - self.height - self.y
        left = self.x
        right = self.source_width - self.width - self.x
        bars = []
        if top:
            bars.append(f"{top}px top")
        if bottom:
            bars.append(f"{bottom}px bottom")
        if left:
            bars.append(f"{left}px left")
        if right:
            bars.append(f"{right}px right")
        return (f"Black bars: {', '.join(bars)} — the real picture is "
                f"{self.width}x{self.height} inside a {self.source_width}x"
                f"{self.source_height} frame.")

    def filter_string(self) -> str:
        return f"crop={self.width}:{self.height}:{self.x}:{self.y}"


def parse_crop(log: str, width: int, height: int) -> CropBox:
    """Reads cropdetect's answer out of a log.

    Run with `reset=0` over the whole file, cropdetect accumulates the largest picture it
    ever saw, so a film that fades in from black converges on the full frame rather than
    on the two dark seconds at the start. That is why this is one pass over everything
    instead of a few samples: sampling is what gets fades wrong.
    """
    found = _CROP_LINE.findall(log)
    if not found:
        return CropBox(source_width=width, source_height=height)
    w, h, x, y = (int(value) for value in found[-1])
    # Encoders want even dimensions, and the crop can never exceed the source.
    return CropBox(min(width, w // 2 * 2), min(height, h // 2 * 2), x, y, width, height)


CROP_FILTER = "cropdetect=limit=24:round=2:reset=0"


def detect_crop(locator: FfmpegLocator, source: Path, duration: float, width: int,
                height: int) -> CropBox:
    """Standalone crop detection, for callers not already decoding the file."""
    if duration <= 0 or width <= 0:
        return CropBox(source_width=width, source_height=height)
    log = _run(locator, [locator.ffmpeg, "-hide_banner", "-nostats", "-i", str(source),
                         "-vf", CROP_FILTER, "-f", "null", "-"])
    return parse_crop(log, width, height)


# ======================================================================================
# Camera shake
# ======================================================================================

SHAKE_WINDOW = 256      # analysis size; the metric is in pixels at this scale


@dataclass
class ShakeReading:
    jerk: float = 0.0          # median frame-to-frame change in the shift vector
    drift: float = 0.0         # median shift magnitude, which a pan also produces
    samples: int = 0

    @property
    def word(self) -> str:
        if self.samples == 0:
            return "not measured"
        if self.jerk < 5:
            return "steady"
        if self.jerk < 15:
            return "slightly shaky"
        if self.jerk < 35:
            return "shaky"
        return "very shaky"

    @property
    def worth_stabilising(self) -> bool:
        return self.samples > 0 and self.jerk >= 5

    def explain(self) -> str:
        if self.samples == 0:
            return "Camera movement was not measured."
        if not self.worth_stabilising and self.drift > 8:
            return (f"The camera moves ({self.drift:.1f}px between frames) but smoothly "
                    f"— that reads as a deliberate pan, not shake, so it is left alone.")
        return (f"Camera movement reads {self.word} (jerk {self.jerk:.1f}px, "
                f"drift {self.drift:.1f}px, {self.samples} frame pairs).")


def _burst(locator: FfmpegLocator, source: Path, at: float, count: int):
    """A run of consecutive frames as grayscale arrays. Consecutive is the point --
    shake lives between neighbouring frames, so spread-out samples cannot see it."""
    try:
        import numpy as np
    except ImportError:
        return None
    size = SHAKE_WINDOW
    result = subprocess.run(
        [locator.ffmpeg, "-v", "error", "-ss", f"{max(0.0, at):.3f}", "-i", str(source),
         "-frames:v", str(count),
         "-vf", f"format=gray,scale={size}:{size}", "-f", "rawvideo", "-"],
        capture_output=True, creationflags=_NO_WINDOW,
    )
    raw = np.frombuffer(result.stdout, dtype=np.uint8)
    frames = len(raw) // (size * size)
    if frames < 2:
        return None
    return raw[:frames * size * size].reshape(frames, size, size).astype(np.float32)


def _phase_shift(first, second, window):
    """Global translation between two frames, by phase correlation."""
    import numpy as np

    size = first.shape[0]
    a = np.fft.fft2(first * window)
    b = np.fft.fft2(second * window)
    cross = a * np.conj(b)
    magnitude = np.abs(cross)
    magnitude[magnitude == 0] = 1e-9
    correlation = np.fft.ifft2(cross / magnitude).real
    peak = np.unravel_index(int(np.argmax(correlation)), correlation.shape)
    dy = peak[0] - size if peak[0] > size // 2 else peak[0]
    dx = peak[1] - size if peak[1] > size // 2 else peak[1]
    return float(dx), float(dy)


def measure_shake(locator: FfmpegLocator, source: Path, duration: float,
                  bursts: int = 3, per_burst: int = 10,
                  skip: list | None = None) -> ShakeReading:
    """How much the camera jitters, ignoring deliberate movement.

    The shift between two frames does not distinguish a shaky hand from a tripod pan --
    both move the whole picture. What separates them is how fast the movement *changes*:
    a pan holds its direction, shake reverses constantly. So the reported figure is the
    median magnitude of the change in the shift vector.
    """
    try:
        import numpy as np
    except ImportError:
        return ShakeReading()
    if duration <= 0:
        return ShakeReading()

    window = np.outer(np.hanning(SHAKE_WINDOW), np.hanning(SHAKE_WINDOW))
    from . import repair as repair_module

    points = repair_module.sample_times(duration, skip or [], bursts)
    vectors: list[tuple[float, float]] = []
    for at in points:
        frames = _burst(locator, source, at, per_burst)
        if frames is None:
            continue
        for index in range(len(frames) - 1):
            vectors.append(_phase_shift(frames[index], frames[index + 1], window))

    if len(vectors) < 3:
        return ShakeReading(samples=len(vectors))

    array = np.array(vectors, dtype=float)
    drift = float(np.median(np.hypot(array[:, 0], array[:, 1])))
    changes = np.diff(array, axis=0)
    jerk = float(np.median(np.hypot(changes[:, 0], changes[:, 1])))
    return ShakeReading(jerk=jerk, drift=drift, samples=len(vectors))


# ======================================================================================
# Interlacing
# ======================================================================================

_IDET_LINE = re.compile(
    r"Multi frame detection:\s*TFF:\s*(\d+)\s*BFF:\s*(\d+)\s*Progressive:\s*(\d+)"
    r"\s*Undetermined:\s*(\d+)"
)

# One field order has to dominate. Detailed progressive footage produces a near-even
# split of TFF and BFF, which counted naively looks like heavy interlacing.
FIELD_DOMINANCE = 0.70


@dataclass
class InterlaceReading:
    tff: int = 0
    bff: int = 0
    progressive: int = 0
    undetermined: int = 0

    @property
    def total(self) -> int:
        return self.tff + self.bff + self.progressive + self.undetermined

    @property
    def dominance(self) -> float:
        return max(self.tff, self.bff) / self.total if self.total else 0.0

    @property
    def interlaced(self) -> bool:
        return self.total > 0 and self.dominance >= FIELD_DOMINANCE

    @property
    def field_order(self) -> str:
        if not self.interlaced:
            return ""
        return "top field first" if self.tff >= self.bff else "bottom field first"

    def explain(self) -> str:
        if self.total == 0:
            return "Interlacing was not measured."
        if self.interlaced:
            return (f"Interlaced, {self.field_order} ({self.dominance * 100:.0f}% of "
                    f"frames agree). Left alone it shows comb teeth on movement.")
        if max(self.tff, self.bff) > self.progressive:
            return (f"Progressive. The detector saw a mix of field orders "
                    f"({self.tff} vs {self.bff}), which fine detail causes — real "
                    f"interlacing is overwhelmingly one order, so it is not deinterlaced.")
        return "Progressive."


def parse_interlace(log: str) -> InterlaceReading:
    matches = _IDET_LINE.findall(log)
    if not matches:
        return InterlaceReading()
    tff, bff, progressive, undetermined = (int(value) for value in matches[-1])
    return InterlaceReading(tff, bff, progressive, undetermined)


def detect_interlace(locator: FfmpegLocator, source: Path,
                     frames: int = 200) -> InterlaceReading:
    log = _run(locator, [
        locator.ffmpeg, "-hide_banner", "-nostats", "-i", str(source),
        "-frames:v", str(frames), "-vf", "idet", "-f", "null", "-",
    ])
    return parse_interlace(log)


# ======================================================================================
# Audio level
# ======================================================================================

_LUFS = re.compile(r"^\s*I:\s*(-?[\d.]+)\s*LUFS", re.MULTILINE)
_LRA = re.compile(r"^\s*LRA:\s*(-?[\d.]+)\s*LU", re.MULTILINE)
_PEAK_DB = re.compile(r"Peak level dB:\s*(-?[\d.inf]+)")
_PEAK_COUNT = re.compile(r"Abs Peak count:\s*([\d.]+)")
_SAMPLES = re.compile(r"Number of samples:\s*([\d.]+)")
_NOISE_FLOOR = re.compile(r"Noise floor dB:\s*(-?[\d.inf]+)")

# Streaming platforms normalise to about -14 LUFS; broadcast uses -23. Anything much
# quieter than the target makes the viewer reach for the volume, and anything louder
# gets turned down by the platform anyway.
LOUDNESS_TARGETS = {
    "Web / YouTube (-14 LUFS)": -14.0,
    "Podcast (-16 LUFS)": -16.0,
    "Broadcast EBU R128 (-23 LUFS)": -23.0,
}
DEFAULT_LOUDNESS = "Web / YouTube (-14 LUFS)"

# A handful of samples touching full scale is normal; a substantial fraction means the
# recording was driven into the limit and the peaks are flattened.
CLIPPING_SHARE = 0.0005


@dataclass
class AudioReading:
    present: bool = False
    lufs: float = 0.0
    lra: float = 0.0
    peak_db: float = 0.0
    clipped_samples: int = 0
    total_samples: int = 0
    noise_floor_db: float = 0.0

    @property
    def clipping_share(self) -> float:
        return self.clipped_samples / self.total_samples if self.total_samples else 0.0

    @property
    def is_clipping(self) -> bool:
        return self.present and self.clipping_share >= CLIPPING_SHARE

    def offset_from(self, target: float) -> float:
        return target - self.lufs

    def loudness_word(self, target: float) -> str:
        difference = self.offset_from(target)
        if abs(difference) < 1.5:
            return "about right"
        if difference > 12:
            return "far too quiet"
        if difference > 0:
            return "too quiet"
        if difference < -12:
            return "far too loud"
        return "too loud"

    def explain(self, target: float) -> str:
        if not self.present:
            return "This file has no audio track."
        parts = [f"Loudness {self.lufs:.1f} LUFS — {self.loudness_word(target)} against "
                 f"a {target:.0f} LUFS target ({self.offset_from(target):+.1f} dB)."]
        if self.is_clipping:
            parts.append(
                f"{self.clipping_share * 100:.2f}% of samples sit at full scale, so the "
                f"recording is clipped. Turning it down cannot undo that — the peaks are "
                f"already flat — but it stops it distorting further."
            )
        return " ".join(parts)


AUDIO_FILTERS = "ebur128=peak=true,astats"


def parse_audio(log: str, has_audio: bool) -> AudioReading:
    """Loudness, range, peak and clipping, out of a log."""
    if not has_audio:
        return AudioReading(present=False)

    def number(pattern, default=0.0) -> float:
        match = pattern.search(log)
        if not match:
            return default
        try:
            return float(match.group(1))
        except ValueError:      # astats prints "-inf" for a silent track
            return default

    return AudioReading(
        present=True,
        lufs=number(_LUFS, -70.0),
        lra=number(_LRA),
        peak_db=number(_PEAK_DB, -70.0),
        clipped_samples=int(number(_PEAK_COUNT)),
        total_samples=int(number(_SAMPLES)),
        noise_floor_db=number(_NOISE_FLOOR, -70.0),
    )


def measure_audio(locator: FfmpegLocator, source: Path, has_audio: bool) -> AudioReading:
    """Standalone audio measurement, for callers not already decoding the file."""
    if not has_audio:
        return AudioReading(present=False)
    log = _run(locator, [
        locator.ffmpeg, "-hide_banner", "-nostats", "-i", str(source),
        "-af", AUDIO_FILTERS, "-f", "null", "-",
    ])
    return parse_audio(log, has_audio)
