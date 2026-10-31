"""Vulkan device discovery, model choice and honest cost estimates for the ncnn tools.

Three things this exists to stop:

  1. **Running on the wrong GPU.** ncnn's "auto" device picked the Intel iGPU on the
     development laptop, with an RTX sitting idle next to it. Measured warm over a batch
     of 24 frames: 0.338s per frame on the iGPU against 0.190s on the RTX, so about 1.8x.
     Worth taking, and free.

  2. **Using the wrong model.** Real-ESRGAN's default is `realesr-animevideov3`, which is
     trained on animation. On photographic footage `realesrgan-x4plus` looks better and
     costs nearly twelve times as much per frame. Both are defensible choices; silently
     making one of them for the user is not.

  3. **Starting a job with no idea how long it is.** An upscale is per-frame work, so a
     measurement on real frames predicts the whole video closely.

A warning about measuring, learned the hard way here: timing ONE image is worthless.
Process start, Vulkan init and model load cost 1.6-3.4 seconds before a pixel moves, and
the first run on a cold driver shader cache is slower still -- a single-frame timing of
these models came out eight times the true per-frame cost, and made the iGPU look four
times slower than it is. Everything below times two batch sizes and takes the slope, so
the fixed cost cancels out exactly.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

# `[1 NVIDIA RTX 2000 Ada Generation Laptop GPU]  queueC=2[8]  queueG=0[16] ...`
_DEVICE_LINE = re.compile(r"^\[(\d+)\s+(.+?)\]\s+queue", re.MULTILINE)

# Integrated parts announce themselves. Matching the integrated names is far more reliable
# than trying to enumerate every discrete card ever made.
_INTEGRATED_MARKERS = (
    "iris", "uhd graphics", "hd graphics", "integrated", "llvmpipe", "microsoft basic",
    "radeon(tm) graphics", "radeon graphics", "vega 3", "vega 6", "vega 7", "vega 8",
    "vega 10", "vega 11", "apple m",
)
_DISCRETE_MARKERS = ("nvidia", "geforce", "rtx", "gtx", "quadro", "tesla", "radeon rx",
                     "radeon pro", "arc a", "arc b")


@dataclass(frozen=True)
class Device:
    index: int
    name: str

    @property
    def integrated(self) -> bool:
        lowered = self.name.lower()
        if any(marker in lowered for marker in _INTEGRATED_MARKERS):
            # "Radeon RX" beats the generic "radeon graphics" match.
            return not any(marker in lowered for marker in _DISCRETE_MARKERS)
        return False

    @property
    def discrete(self) -> bool:
        lowered = self.name.lower()
        return any(marker in lowered for marker in _DISCRETE_MARKERS) and not self.integrated

    def label(self) -> str:
        kind = "discrete" if self.discrete else ("integrated" if self.integrated else "GPU")
        return f"{self.index}: {self.name} ({kind})"


# ======================================================================================
# Models
# ======================================================================================

@dataclass(frozen=True)
class UpscaleModel:
    id: str
    label: str
    suits: str
    relative_cost: float     # multiples of the fastest model, measured on real frames
    scales: tuple[int, ...]


# realesr-animevideov3 ships x2/x3/x4 weights. realesrgan-x4plus is x4 only; the tool will
# still honour -s 2 by upscaling x4 and resampling down, which costs the same as x4.
# relative_cost is measured, not estimated: slope over 8 vs 24 frames of 640x360 at x4
# on an RTX 2000 Ada. animevideov3 0.117 s/frame, x4plus-anime 0.400, x4plus 1.373.
REALESRGAN_MODELS = (
    UpscaleModel("realesrgan-x4plus", "Photo & film (best detail)",
                 "camera footage, renders, anything photographic", 11.7, (2, 3, 4)),
    UpscaleModel("realesr-animevideov3", "Animation & video (fast)",
                 "drawn animation, screen recordings, and anything you need finished today",
                 1.0, (2, 3, 4)),
    UpscaleModel("realesrgan-x4plus-anime", "Anime art (sharp lines)",
                 "anime stills and line art", 3.4, (2, 3, 4)),
    UpscaleModel("realesrnet-x4plus", "Photo, gentler (less invented detail)",
                 "footage where you want it clean rather than dramatic", 11.7, (2, 3, 4)),
)

DEFAULT_MODEL = "realesrgan-x4plus"


def models_for(tool_id: str) -> tuple[UpscaleModel, ...]:
    if tool_id == "realesrgan":
        return REALESRGAN_MODELS
    return ()


def model(model_id: str) -> UpscaleModel | None:
    return next((m for m in REALESRGAN_MODELS if m.id == model_id), None)


def available_models(tool) -> tuple[UpscaleModel, ...]:
    """Only the models whose weights are actually on disk.

    A dropdown entry for a model the download did not include produces a tool that exits
    with a file-not-found and no explanation.
    """
    executable = tool.executable_path() if tool else None
    if executable is None:
        return models_for(getattr(tool, "id", ""))
    folder = executable.parent / "models"
    if not folder.is_dir():
        return models_for(getattr(tool, "id", ""))
    present = {path.stem for path in folder.glob("*.param")}
    usable = []
    for candidate in models_for(tool.id):
        # weights are named either `<id>.param` or `<id>-x<scale>.param`
        if candidate.id in present or any(
            name.startswith(f"{candidate.id}-x") for name in present
        ):
            usable.append(candidate)
    return tuple(usable) or models_for(tool.id)


# ======================================================================================
# Device discovery
# ======================================================================================

_DEVICE_CACHE: dict[str, list[Device]] = {}


def _probe_image() -> Path:
    """A tiny PNG, written once, purely so the tool has something to chew on."""
    target = Path(tempfile.gettempdir()) / "mediaforge_gpu_probe.png"
    if target.exists():
        return target
    # A 16x16 mid-grey PNG, hand-assembled so this does not depend on Pillow.
    import struct
    import zlib

    width = height = 16
    raw = b"".join(b"\x00" + bytes([128, 128, 128] * width) for _ in range(height))

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    target.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    return target


def devices(tool, refresh: bool = False) -> list[Device]:
    """Every Vulkan device the tool can see, in the tool's own numbering.

    The list only appears in the banner of a real run, so a 16x16 probe image gets run
    through it. That costs about a second, once per session.
    """
    executable = tool.executable_path() if tool else None
    if executable is None:
        return []
    key = str(executable)
    if not refresh and key in _DEVICE_CACHE:
        return _DEVICE_CACHE[key]

    source = _probe_image()
    with tempfile.TemporaryDirectory() as work:
        result = subprocess.run(
            [str(executable), "-i", str(source), "-o", str(Path(work) / "out.png"), "-s", "4"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=_NO_WINDOW, timeout=180,
        )
    log = (result.stderr or "") + (result.stdout or "")
    seen: dict[int, str] = {}
    for match in _DEVICE_LINE.finditer(log):
        seen.setdefault(int(match.group(1)), match.group(2).strip())
    found = [Device(index, name) for index, name in sorted(seen.items())]
    _DEVICE_CACHE[key] = found
    return found


def device_by_index(tool, index: "int | None") -> Device | None:
    """Looks a device up by its number, keeping its real name.

    Building a Device from an index alone leaves the name empty, and the name is what the
    estimate sentence prints -- "0.14s per frame on ." is worse than saying nothing.
    """
    if index is None:
        return None
    return next((device for device in devices(tool) if device.index == int(index)), None)


def best_device(tool) -> Device | None:
    """Prefer a discrete GPU. This is the pick ncnn's own 'auto' gets wrong."""
    found = devices(tool)
    if not found:
        return None
    discrete = [device for device in found if device.discrete]
    if discrete:
        return discrete[0]
    unknown = [device for device in found if not device.integrated]
    return unknown[0] if unknown else found[0]


def describe_devices(tool) -> str:
    found = devices(tool)
    if not found:
        return "No Vulkan device found. The upscaler needs a GPU with current drivers."
    chosen = best_device(tool)
    lines = [device.label() + ("   ← will be used" if device == chosen else "")
             for device in found]
    if chosen and chosen.index != 0 and any(d.index == 0 for d in found):
        lines.append("")
        lines.append("The tool's own default is device 0. On a laptop that is usually the "
                     "integrated chip, which is several times slower, so it is overridden "
                     "here.")
    return "\n".join(lines)


# ======================================================================================
# Benchmarking
# ======================================================================================

@dataclass
class Estimate:
    seconds_per_frame: float
    frames: int
    device: Device | None
    model_id: str
    measured: bool = False

    @property
    def total_seconds(self) -> float:
        return self.seconds_per_frame * self.frames

    def pretty_total(self) -> str:
        total = self.total_seconds
        if total < 90:
            return f"{total:.0f} seconds"
        if total < 5400:
            return f"{total / 60:.0f} minutes"
        return f"{total / 3600:.1f} hours"

    def sentence(self) -> str:
        where = f" on {self.device.name}" if self.device else ""
        how = "Measured" if self.measured else "Rough guess"
        return (f"{how}: about {self.pretty_total()} for {self.frames} frames "
                f"({self.seconds_per_frame:.2f}s per frame{where}).")


def _run_batch(executable: Path, frames: list[Path], scale: int, model_id: str,
               device: "Device | None") -> float:
    """Wall time for one invocation over a directory of frames."""
    with tempfile.TemporaryDirectory() as work:
        staging = Path(work) / "in"
        output = Path(work) / "out"
        staging.mkdir()
        output.mkdir()
        for index, frame in enumerate(frames):
            shutil.copyfile(frame, staging / f"{index:04d}.png")
        argv = [str(executable), "-i", str(staging), "-o", str(output),
                "-s", str(scale), "-n", model_id, "-f", "png"]
        if device is not None:
            argv += ["-g", str(device.index)]
        started = time.perf_counter()
        result = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", creationflags=_NO_WINDOW, timeout=3600)
        elapsed = time.perf_counter() - started
        produced = len(list(output.glob("*.png")))
    if result.returncode != 0 or produced < len(frames):
        raise RuntimeError(
            f"The upscaler failed on a test batch ({produced}/{len(frames)} frames): "
            + (result.stderr or "")[-500:]
        )
    return elapsed


SMALL_BATCH = 3
LARGE_BATCH = 9


def benchmark_frame(tool, sample: Path, scale: int = 4, model_id: str = DEFAULT_MODEL,
                    device: "Device | None" = None,
                    progress=lambda fraction, message: None) -> float:
    """Marginal seconds per frame: the slope between a small and a large batch.

    Not the time for one image. One image is mostly process start, Vulkan init and model
    load -- between 1.6 and 3.4 seconds here, against a true per-frame cost of 0.12 to
    1.37 depending on the model. Timing two batch sizes and dividing the DIFFERENCE by
    the difference in frames removes that fixed cost exactly, instead of baking an
    eightfold overestimate into every quoted duration.

    The sample is a real frame from the real video: cost scales with pixel count, so a
    stand-in of the wrong size predicts the wrong number.
    """
    executable = tool.executable_path() if tool else None
    if executable is None:
        raise RuntimeError("The upscaler is not installed.")
    device = device or best_device(tool)

    progress(0.1, "Warming up the GPU")
    # The first run on a cold driver shader cache is slower than every run after it, so
    # the small batch doubles as the warm-up and its timing is taken on the second pass.
    _run_batch(executable, [sample] * SMALL_BATCH, scale, model_id, device)

    progress(0.4, f"Timing {SMALL_BATCH} frames")
    small = _run_batch(executable, [sample] * SMALL_BATCH, scale, model_id, device)
    progress(0.7, f"Timing {LARGE_BATCH} frames")
    large = _run_batch(executable, [sample] * LARGE_BATCH, scale, model_id, device)
    progress(1.0, "Measured")

    slope = (large - small) / (LARGE_BATCH - SMALL_BATCH)
    # A slope at or below zero means the timings were noise, not signal; fall back to the
    # large batch's average, which at least errs on the side of quoting too long.
    return slope if slope > 0.001 else large / LARGE_BATCH


def startup_seconds(tool, sample: Path, scale: int = 4,
                    model_id: str = DEFAULT_MODEL, device: "Device | None" = None) -> float:
    """The fixed cost per invocation, for reporting. Uses the same two-point fit."""
    executable = tool.executable_path() if tool else None
    if executable is None:
        return 0.0
    device = device or best_device(tool)
    _run_batch(executable, [sample] * SMALL_BATCH, scale, model_id, device)
    small = _run_batch(executable, [sample] * SMALL_BATCH, scale, model_id, device)
    large = _run_batch(executable, [sample] * LARGE_BATCH, scale, model_id, device)
    slope = (large - small) / (LARGE_BATCH - SMALL_BATCH)
    return max(0.0, small - SMALL_BATCH * slope)


def estimate(tool, sample: Path, frames: int, scale: int = 4,
             model_id: str = DEFAULT_MODEL, device: "Device | None" = None,
             progress=lambda fraction, message: None) -> Estimate:
    device = device or best_device(tool)
    per_frame = benchmark_frame(tool, sample, scale, model_id, device, progress)
    return Estimate(seconds_per_frame=per_frame, frames=frames, device=device,
                    model_id=model_id, measured=True)


def rough_estimate(frames: int, model_id: str = DEFAULT_MODEL,
                   device: Device | None = None) -> Estimate:
    """A guess for when there is no time to measure. Labelled as a guess by the caller.

    Anchored on the measured 0.117 s/frame for animevideov3 at x4 on a 640x360 frame with
    a laptop RTX, scaled by the model's relative cost. It says nothing about the user's
    resolution or hardware, which is exactly why `estimate` exists -- callers must show
    this as the guess it is.
    """
    entry = model(model_id)
    per_frame = 0.117 * (entry.relative_cost if entry else 1.0)
    if device is not None and device.integrated:
        per_frame *= 1.8
    return Estimate(per_frame, frames, device, model_id, measured=False)
