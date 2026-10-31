"""Look at a photo and work out what to do with it.

Runs locally on Pillow + numpy -- no API, no key, no network. The numbers it extracts are
deliberately simple and explainable, because the point is not to be clever but to make a
defensible recommendation the user can see the reasoning for and override.

What it produces:
  * a scene classification (exterior day/night, interior, aerial, detail, ...)
  * a recommended camera move, with a direction that suits the composition
  * a recommended atmosphere treatment
  * a written prompt for image-to-video models, phrased the way they expect
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import numpy
    from PIL import Image, ImageFilter, ImageOps
    ANALYSIS_AVAILABLE = True
except ImportError:  # pragma: no cover - Pillow/numpy missing
    ANALYSIS_AVAILABLE = False

WORKING_EDGE = 480          # analyse at this longest edge; plenty for statistics


@dataclass
class SceneAnalysis:
    path: Path
    width: int = 0
    height: int = 0

    brightness: float = 0.5          # 0 dark .. 1 bright
    contrast: float = 0.5
    saturation: float = 0.5
    warmth: float = 0.0              # -1 cool/blue .. +1 warm/orange
    detail: float = 0.5              # edge density
    flatness: float = 0.5            # share of the frame that is smooth, unbroken surface
    sky_fraction: float = 0.0        # how much of the top band reads as sky
    top_sky: float = 0.0             # sky in the top 15% only -- stricter, less fooled
    vertical_bias: float = 0.0       # -1 horizontal lines .. +1 vertical lines
    subject_x: float = 0.5           # centre of visual interest, 0..1
    subject_y: float = 0.5
    horizon_y: float = 0.5

    scene: str = "unknown"
    scene_confidence: float = 0.0
    is_night: bool = False
    is_interior: bool = False
    is_portrait: bool = False

    move: str = "Zoom in"
    move_reason: str = ""
    # Ranked fallbacks, best first. A sequence of shots that all deserve the same move
    # still needs variety, so the planner walks down this list rather than repeating.
    move_alternatives: list[str] = field(default_factory=list)
    motion_strength: float = 0.18
    atmosphere: str = "None"
    atmosphere_reason: str = ""
    suggested_seconds: float = 4.0
    ai_prompt: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def aspect(self) -> float:
        return self.width / self.height if self.height else 1.0

    def summary(self) -> str:
        return (
            f"{SCENE_LABELS.get(self.scene, self.scene)} · "
            f"{'night' if self.is_night else 'daylight'} · "
            f"move: {self.move} · atmosphere: {self.atmosphere}"
        )

    def explanation(self) -> str:
        lines = [
            f"Detected: {SCENE_LABELS.get(self.scene, self.scene)}"
            f"  (confidence {self.scene_confidence:.0%})",
            "",
            f"Brightness {self.brightness:.2f}   contrast {self.contrast:.2f}   "
            f"saturation {self.saturation:.2f}",
            f"Warmth {self.warmth:+.2f}   detail {self.detail:.2f}   "
            f"flat surfaces {self.flatness:.0%}   sky {self.sky_fraction:.0%}",
            f"Lines {'mostly vertical' if self.vertical_bias > 0.15 else ('mostly horizontal' if self.vertical_bias < -0.15 else 'mixed')}"
            f"   subject at ({self.subject_x:.2f}, {self.subject_y:.2f})",
            "",
            f"Move: {self.move}",
            f"  {self.move_reason}",
            f"Atmosphere: {self.atmosphere}",
            f"  {self.atmosphere_reason}",
        ]
        if self.notes:
            lines += ["", *[f"- {note}" for note in self.notes]]
        return "\n".join(lines)


SCENE_LABELS = {
    "exterior_day": "Exterior, daylight",
    "exterior_night": "Exterior, night",
    "exterior_golden": "Exterior, golden hour",
    "aerial": "Aerial / rooftop",
    "interior_bright": "Interior, bright",
    "interior_warm": "Interior, warm and low-lit",
    "detail": "Detail / close-up",
    "unknown": "Unclassified",
}


def analyse(path: Path) -> SceneAnalysis:
    """Never raises: a failed read still returns a usable default plan."""
    result = SceneAnalysis(path=path)
    if not ANALYSIS_AVAILABLE:
        result.notes.append("Pillow/numpy unavailable — using generic defaults.")
        _recommend(result)
        return result

    try:
        with Image.open(path) as handle:
            image = ImageOps.exif_transpose(handle).convert("RGB")
            result.width, result.height = image.size
            image.thumbnail((WORKING_EDGE, WORKING_EDGE), Image.Resampling.BILINEAR)
            _measure(image, result)
    except Exception as error:
        result.notes.append(f"Could not analyse the image ({error}); using defaults.")
        _recommend(result)
        return result

    _classify(result)
    _recommend(result)
    return result


# --------------------------------------------------------------------------------------
# Measurement
# --------------------------------------------------------------------------------------

def _measure(image: "Image.Image", result: SceneAnalysis) -> None:
    pixels = numpy.asarray(image, dtype=numpy.float32) / 255.0
    red, green, blue = pixels[..., 0], pixels[..., 1], pixels[..., 2]

    luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue
    result.brightness = float(luminance.mean())

    low, high = numpy.percentile(luminance, [5, 95])
    result.contrast = float(numpy.clip(high - low, 0.0, 1.0))

    peak = pixels.max(axis=2)
    trough = pixels.min(axis=2)
    result.saturation = float(numpy.mean((peak - trough) / numpy.maximum(peak, 1e-4)))

    # Warmth as the red/blue imbalance -- a robust stand-in for colour temperature.
    result.warmth = float(numpy.clip((red.mean() - blue.mean()) * 3.0, -1.0, 1.0))

    edges = numpy.asarray(
        image.convert("L").filter(ImageFilter.FIND_EDGES), dtype=numpy.float32
    ) / 255.0
    edges[0, :] = edges[-1, :] = edges[:, 0] = edges[:, -1] = 0.0   # border artefacts
    result.detail = float(numpy.clip(edges.mean() * 6.0, 0.0, 1.0))

    # Gradient balance tells vertical architecture from horizontal landscape.
    grey = numpy.asarray(image.convert("L"), dtype=numpy.float32) / 255.0
    d_x = numpy.abs(numpy.diff(grey, axis=1)).mean()
    d_y = numpy.abs(numpy.diff(grey, axis=0)).mean()
    total = d_x + d_y
    # Strong vertical LINES produce large horizontal gradients, hence d_x on top.
    result.vertical_bias = float((d_x - d_y) / total) if total > 1e-6 else 0.0

    # Flatness separates rooms from everything else: an interior is mostly unbroken wall,
    # ceiling and floor, while a facade or an aerial is textured edge to edge.
    result.flatness = float((edges < 0.045).mean())

    height, width = luminance.shape

    def sky_share(fraction: float) -> float:
        rows = max(1, int(height * fraction))
        band, band_blue, band_red = luminance[:rows], blue[:rows], red[:rows]
        mask = (band > 0.45) & ((band_blue >= band_red - 0.02) | (band > 0.80))
        return float(mask.mean())

    result.sky_fraction = sky_share(0.40)
    result.top_sky = sky_share(0.15)

    # Visual interest = where the edges are. Weighted centroid of edge energy.
    weight = edges + 1e-6
    ys, xs = numpy.mgrid[0:height, 0:width]
    result.subject_x = float((xs * weight).sum() / weight.sum() / max(1, width - 1))
    result.subject_y = float((ys * weight).sum() / weight.sum() / max(1, height - 1))

    # Horizon: the row where brightness drops off fastest in the upper half.
    row_means = luminance.mean(axis=1)
    if len(row_means) > 8:
        deltas = numpy.diff(row_means)
        upper = deltas[: max(1, int(len(deltas) * 0.75))]
        result.horizon_y = float((int(numpy.argmin(upper)) + 1) / max(1, height - 1))

    result.is_portrait = result.height > result.width


# --------------------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------------------

def _classify(result: SceneAnalysis) -> None:
    result.is_night = result.brightness < 0.28
    has_sky = result.sky_fraction > 0.10

    # Busy edge-to-edge frames are never rooms, however little sky they show. Without
    # this an aerial looking straight down reads as "interior" purely for lacking sky.
    busy = result.flatness < 0.70 and result.detail > 0.55
    result.is_interior = not has_sky and not busy and result.brightness < 0.72

    scores: dict[str, float] = {}

    # Aerial: dense texture, no horizon, no dominant verticals, little or no sky at top.
    scores["aerial"] = (
        0.35 * float(result.top_sky < 0.10)
        + 0.30 * float(result.detail > 0.50)
        + 0.20 * float(result.flatness < 0.70)
        + 0.15 * float(abs(result.vertical_bias) < 0.14)
    )
    scores["exterior_night"] = (
        0.50 * float(result.is_night) + 0.25 * float(has_sky)
        + 0.15 * float(result.contrast > 0.45) + 0.10 * float(not result.is_interior)
    )
    scores["exterior_golden"] = (
        0.40 * float(has_sky) + 0.30 * float(result.warmth > 0.12)
        + 0.20 * float(0.28 <= result.brightness <= 0.62)
        + 0.10 * float(result.top_sky > 0.25)
    )
    scores["exterior_day"] = (
        0.45 * float(has_sky) + 0.30 * float(result.brightness >= 0.45)
        + 0.25 * float(result.top_sky > 0.30)
    )
    scores["interior_warm"] = (
        0.35 * float(result.is_interior) + 0.25 * float(result.warmth > 0.06)
        + 0.20 * float(result.brightness < 0.5) + 0.20 * float(result.flatness > 0.55)
    )
    scores["interior_bright"] = (
        0.40 * float(result.is_interior) + 0.35 * float(result.brightness >= 0.5)
        + 0.25 * float(result.flatness > 0.55)
    )
    # Detail: one close subject, so plenty of smooth ground around a small busy area.
    scores["detail"] = (
        0.35 * float(result.detail > 0.55) + 0.25 * float(not has_sky)
        + 0.20 * float(result.contrast < 0.55) + 0.20 * float(result.flatness > 0.62)
    )

    best = max(scores.items(), key=lambda item: item[1])
    result.scene = best[0] if best[1] > 0.34 else "unknown"
    result.scene_confidence = min(1.0, best[1])


# --------------------------------------------------------------------------------------
# Recommendation
# --------------------------------------------------------------------------------------

def _recommend(result: SceneAnalysis) -> None:
    """Picks a move, and ranks the runners-up.

    Earlier versions returned "Zoom in" for almost everything, because the cues that
    would have chosen otherwise (an off-centre subject, a tall frame) rarely fire on
    architectural renders, which are composed dead centre. The cues below use what such
    images actually vary in: sky, aspect and where the horizon sits.
    """
    scene = result.scene
    offset = result.subject_x - 0.5
    horizontal = "Pan right" if offset >= 0 else "Pan left"
    away = "Pan left" if offset >= 0 else "Pan right"

    if scene == "aerial":
        result.move = "Zoom out"
        result.move_reason = ("Looking down from above — pulling back reveals the roof and "
                              "the streets around it.")
        result.move_alternatives = ["Pan down", horizontal, "Zoom in"]

    elif result.is_portrait and result.vertical_bias > 0.05:
        result.move = "Pan up"
        result.move_reason = ("Tall frame with strong vertical lines — rising up the facade "
                              "follows the architecture.")
        result.move_alternatives = ["Zoom in + pan up", "Zoom in", "Pan down"]

    elif result.sky_fraction > 0.45:
        result.move = "Zoom in + pan up"
        result.move_reason = (f"{result.sky_fraction:.0%} of the upper frame is sky — "
                              f"rising into it while pushing in opens the shot up.")
        result.move_alternatives = ["Pan up", "Zoom in", horizontal]

    elif result.aspect > 1.7:
        result.move = horizontal
        result.move_reason = ("A wide frame reads better travelling across it than pushing "
                              "into the middle.")
        result.move_alternatives = ["Zoom in", away, "Zoom out"]

    elif abs(offset) > 0.04:
        result.move = horizontal
        result.move_reason = (
            f"The visual weight sits {'right' if offset > 0 else 'left'} of centre — "
            f"drifting that way settles the subject in frame."
        )
        result.move_alternatives = ["Zoom in", "Zoom in + pan up", away]

    elif scene in ("interior_warm", "interior_bright"):
        result.move = "Zoom in"
        result.move_reason = "Interior with depth — a gentle push walks the viewer into the room."
        result.move_alternatives = [horizontal, "Pan down", "Zoom out"]

    elif scene == "detail":
        result.move = "Zoom in"
        result.move_reason = "Close subject — a small push adds life without losing the framing."
        result.move_alternatives = ["Zoom out", horizontal, "Pan up"]

    else:
        result.move = "Zoom in"
        result.move_reason = "No strong cue either way, so the safe classic move."
        result.move_alternatives = [horizontal, "Zoom out", "Zoom in + pan up"]

    # Never offer the chosen move twice, and never offer a still as a "variation".
    result.move_alternatives = [
        candidate for candidate in dict.fromkeys(result.move_alternatives)
        if candidate != result.move
    ]

    # Night and detail want restraint; wide daylight can take more.
    if result.is_night or scene == "detail":
        result.motion_strength = 0.11
    elif scene == "aerial":
        result.motion_strength = 0.22
    else:
        result.motion_strength = 0.16

    # Busy frames need longer on screen to be read.
    result.suggested_seconds = round(
        3.2 + 1.8 * result.detail + (0.6 if result.is_night else 0.0), 1
    )

    if scene == "exterior_night":
        result.atmosphere = "City night glow"
        result.atmosphere_reason = "Dark exterior — lifting the highlights makes windows read as lit."
    elif scene == "exterior_golden":
        result.atmosphere = "Golden haze"
        result.atmosphere_reason = "Warm sky — a soft haze exaggerates the low sun."
    elif scene == "aerial":
        result.atmosphere = "Clean cinematic"
        result.atmosphere_reason = "Aerials read best sharp; just a touch of contrast."
    elif scene in ("interior_warm",):
        result.atmosphere = "Warm bloom"
        result.atmosphere_reason = "Low warm interior — bloom softens the lamps."
    elif scene in ("interior_bright", "detail"):
        result.atmosphere = "Clean cinematic"
        result.atmosphere_reason = "Bright and clean already; only a light grade."
    else:
        result.atmosphere = "None"
        result.atmosphere_reason = "Nothing obvious to add."

    result.ai_prompt = build_ai_prompt(result)


CAMERA_PHRASES = {
    "Zoom in": "the camera pushes in slowly and steadily",
    "Zoom out": "the camera pulls back and rises, revealing more of the surroundings",
    "Pan right": "the camera drifts smoothly to the right",
    "Pan left": "the camera drifts smoothly to the left",
    "Pan up": "the camera tilts and cranes upward along the facade",
    "Pan down": "the camera descends slowly from above",
    "Zoom in + pan up": "the camera pushes in while craning upward",
    "Still (no motion)": "the camera holds completely still",
}

SCENE_PHRASES = {
    "exterior_day": "a modern residential building exterior in daylight",
    "exterior_night": "a modern residential building exterior at night with lit windows",
    "exterior_golden": "a modern building exterior in warm golden-hour light",
    "aerial": "an aerial view over a modern residential building and its rooftop terrace",
    "interior_bright": "a bright contemporary interior space",
    "interior_warm": "a warm, softly lit contemporary interior",
    "detail": "a close architectural detail",
    "unknown": "an architectural scene",
}

ATMOSPHERE_PHRASES = {
    "City night glow": "warm window light glowing, subtle reflections on wet ground, "
                       "faint moving clouds",
    "Golden haze": "soft golden haze in the air, gentle drifting clouds, warm rim light",
    "Rain": "light rain falling, wet reflective ground, subtle ripples",
    "Snow": "soft snow drifting slowly through the air",
    "Fog drift": "thin fog drifting slowly across the frame",
    "Warm bloom": "warm light blooming softly from the fixtures",
    "Clean cinematic": "crisp clean light, subtle depth",
    "None": "",
}


def build_ai_prompt(result: SceneAnalysis) -> str:
    """Phrasing that image-to-video models respond to: subject, motion, atmosphere, limits."""
    subject = SCENE_PHRASES.get(result.scene, SCENE_PHRASES["unknown"])
    camera = CAMERA_PHRASES.get(result.move, CAMERA_PHRASES["Zoom in"])
    weather = ATMOSPHERE_PHRASES.get(result.atmosphere, "")

    parts = [f"{subject}. {camera[0].upper()}{camera[1:]}"]
    if weather:
        parts.append(weather)
    parts.append("photorealistic architectural visualisation, cinematic, steady smooth motion")
    prompt = ", ".join(parts) + "."
    # Models drift badly on stills unless told to preserve the frame.
    prompt += (" Keep the building geometry and materials exactly as in the image; "
               "no morphing, no added or removed structures, no text.")
    return prompt


def analyse_many(paths: list[Path]) -> list[SceneAnalysis]:
    return [analyse(path) for path in paths]


def suggest_order(analyses: list[SceneAnalysis]) -> list[int]:
    """Indices reordered into a watchable arc: wide establishing first, details later,
    night last. Returns the original indices in their suggested order."""
    rank = {
        "aerial": 0, "exterior_day": 1, "exterior_golden": 2, "exterior_night": 6,
        "interior_bright": 3, "interior_warm": 4, "detail": 5, "unknown": 3,
    }
    return sorted(
        range(len(analyses)),
        key=lambda index: (rank.get(analyses[index].scene, 3), -analyses[index].brightness),
    )
