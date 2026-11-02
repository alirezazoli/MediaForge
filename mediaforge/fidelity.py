"""Did the AI only add motion, or did it change the photo?

A generated clip is supposed to be the photo, alive: leaves move, water ripples, light
shifts. Sometimes the model invents instead -- in the feature tour a bottle grew out of
the floor of a living room. That clip should never reach a film. This compares frames of
the clip with the photo it started from, region by region, and rejects a clip where any
region changed more than motion explains.

Measured on real Wan 2.2 5B clips (2026-11-02), worst region after motion is explained:
faithful living room 10.1, faithful lake (Standard) 7.9; a bottle growing out of the
floor 36.8, a figure entering a lake shot (Draft) 94.5. The limit sits between.
"""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .ffmpeg_tools import FfmpegLocator, _NO_WINDOW

WIDTH = 256                 # analysis width; detail below this is motion, not content
GRID = (8, 5)               # columns x rows of regions
SAMPLES_PER_SECOND = 4
# A region's mean difference (0-255, after a slight blur) above which something in it was
# replaced rather than moved. See the module docstring for the measurements.
REGION_LIMIT = 28.0
# The whole picture's mean brightness (0-255) moving this far means the light changed.
FRAME_LIMIT = 18.0


@dataclass
class Verdict:
    faithful: bool
    worst_region: float
    worst_frame: float
    at_seconds: float
    reason: str = ""


def _prepare(image, size: tuple[int, int]):
    """Greyscale, centre-cropped to the clip's shape (as the model crops), blurred."""
    import numpy as np
    from PIL import ImageFilter

    image = image.convert("L")
    ratio = size[0] / size[1]
    width, height = image.size
    if width / height > ratio:
        crop = int(height * ratio)
        image = image.crop(((width - crop) // 2, 0, (width - crop) // 2 + crop, height))
    else:
        crop = int(width / ratio)
        image = image.crop((0, (height - crop) // 2, width, (height - crop) // 2 + crop))
    image = image.resize((WIDTH, max(1, int(WIDTH / ratio)))).filter(ImageFilter.GaussianBlur(2))
    return np.asarray(image, dtype=np.float32)


SEARCH = 6                  # pixels (at WIDTH) a region may have moved and still match
SEARCH_STEP = 2


def unexplained(frame, reference):
    """Per region, the difference left after the best small shift of the photo.

    Comparing pixel to pixel called a 2 % camera zoom on a detailed photo a change (69,
    against a limit of 28): every edge moved a few pixels. Here each region is matched
    against the photo shifted up to SEARCH pixels either way, so motion -- the camera's,
    a branch's, a ripple's -- is explained away, and what is left is what has no
    counterpart in the photo at all.
    """
    import numpy as np

    columns, rows = GRID
    height, width = frame.shape
    padded = np.pad(reference, SEARCH, mode="edge")
    best = np.full((rows, columns), np.inf, dtype=np.float32)
    for dy in range(-SEARCH, SEARCH + 1, SEARCH_STEP):
        for dx in range(-SEARCH, SEARCH + 1, SEARCH_STEP):
            shifted = padded[SEARCH + dy:SEARCH + dy + height, SEARCH + dx:SEARCH + dx + width]
            difference = np.abs(frame - shifted)
            cropped = difference[:height // rows * rows, :width // columns * columns]
            blocks = cropped.reshape(rows, height // rows, columns, width // columns).mean(
                axis=(1, 3))
            best = np.minimum(best, blocks)
    # Light and colour as a whole: how far the picture's overall brightness moved. A zoom
    # or a pan leaves it where it was; a darkened or brightened scene does not.
    light = abs(float(frame.mean()) - float(reference.mean()))
    return best, light


def check(locator: FfmpegLocator, photo: Path, clip: Path) -> Verdict:
    """Compares every quarter-second of the clip with the photo it was made from."""
    import numpy as np
    from PIL import Image

    folder = Path(tempfile.mkdtemp(prefix="mediaforge_fidelity_"))
    subprocess.run([locator.ffmpeg, "-v", "error", "-i", str(clip), "-vf",
                    f"fps={SAMPLES_PER_SECOND}", str(folder / "f%04d.png")],
                   capture_output=True, creationflags=_NO_WINDOW)
    frames = sorted(folder.glob("f*.png"))
    if not frames:
        return Verdict(False, 0.0, 0.0, 0.0, "the clip could not be read")
    try:
        with Image.open(frames[0]) as first:
            size = first.size
        with Image.open(photo) as source:
            reference = _prepare(source, size)
        worst = Verdict(True, 0.0, 0.0, 0.0)
        light_at = 0.0
        for index, frame in enumerate(frames):
            with Image.open(frame) as image:
                regions, whole = unexplained(_prepare(image, size), reference)
            region = float(np.max(regions))
            if region > worst.worst_region:
                worst.worst_region, worst.at_seconds = region, index / SAMPLES_PER_SECOND
            if whole > worst.worst_frame:
                worst.worst_frame, light_at = whole, index / SAMPLES_PER_SECOND
    finally:
        for frame in frames:
            frame.unlink(missing_ok=True)
        folder.rmdir()

    if worst.worst_region > REGION_LIMIT:
        worst.faithful = False
        worst.reason = (f"something in the picture changed or appeared "
                        f"(around {worst.at_seconds:.1f} s)")
    elif worst.worst_frame > FRAME_LIMIT:
        worst.faithful = False
        worst.at_seconds = light_at
        worst.reason = f"the light of the whole picture changed (around {light_at:.1f} s)"
    return worst
