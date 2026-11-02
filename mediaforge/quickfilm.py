"""Quick clip: photos in, a film of the length you ask for out, with nothing invented.

Photos to video asks a dozen questions across four steps; most people want three: how long,
which music, and may the AI make things move. This answers everything else itself, and is
deliberately faithful:

  * the photos stay in the order given and are never repainted, graded or decorated --
    no rain, snow or fog overlays, no colour looks;
  * AI only animates what is already in a photo, and every AI clip is checked against its
    photo (fidelity.py) and dropped for a camera move if anything in it changed;
  * up to three minutes, the length asked for, split evenly across the photos.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from . import autopilot
from .photo_ai import FAITHFUL_PROMPT
from .photofilm import ASPECTS, PhotoFilmSettings

MIN_SECONDS, MAX_SECONDS = 10.0, 180.0
SECONDS_PER_PHOTO = 4.0                  # the default pace when no length is chosen
PER_PHOTO_RANGE = (2.0, 30.0)
CROSSFADE = 0.8

AI_LEVELS = {
    "off": "Off — camera moves only",
    "some": "On some photos",
    "all": "On every photo",
}
SHARE_FOR_SOME = 0.3

SHAPES = {"auto": "Auto (from the photos)", "16:9 landscape": "16:9 landscape",
          "9:16 vertical": "9:16 vertical", "1:1 square": "1:1 square"}

MUSIC_MOODS = {
    "Calm": "calm, warm instrumental, soft piano and acoustic guitar, gentle and hopeful",
    "Uplifting": "uplifting instrumental, bright acoustic guitar, light percussion, positive",
    "Cinematic": "cinematic instrumental, strings and piano, slow build, emotional",
    "Corporate": "modern corporate instrumental, clean synths, steady beat, confident",
    "Lo-fi": "lo-fi chill instrumental, soft electric piano, mellow beat, relaxed",
}


def default_length(photo_count: int) -> float:
    return max(MIN_SECONDS, min(MAX_SECONDS, photo_count * SECONDS_PER_PHOTO))


def seconds_per_photo(length: float, photo_count: int) -> float:
    """Each photo's time on screen so the finished film, crossfades included, is `length`."""
    if photo_count <= 0:
        return 0.0
    length = max(MIN_SECONDS, min(MAX_SECONDS, length))
    each = (length + CROSSFADE * (photo_count - 1)) / photo_count
    return round(max(PER_PHOTO_RANGE[0], min(PER_PHOTO_RANGE[1], each)), 2)


def ai_shot_indices(photo_count: int, level: str) -> list[int]:
    """Which shots move with AI: none, an even spread of about a third, or all of them."""
    if level == "all":
        return list(range(photo_count))
    if level != "some" or photo_count == 0:
        return []
    wanted = max(1, round(photo_count * SHARE_FOR_SOME))
    step = photo_count / wanted
    return sorted({min(photo_count - 1, int(step * index + step / 2)) for index in range(wanted)})


@dataclass
class QuickPlan:
    plan: autopilot.AutoPlan
    length: float
    ai_shots: int
    ai_minutes: float
    notes: list[str]


def plan(photos: Sequence[Path], registry, length: float, ai_level: str = "off",
         shape: str = "auto", music: Path | None = None, learning=None,
         progress=lambda fraction, message: None) -> QuickPlan:
    if not photos:
        raise ValueError("Add at least one photo.")
    notes: list[str] = []
    each = seconds_per_photo(length, len(photos))
    result = autopilot.plan(list(photos), registry, base_settings=PhotoFilmSettings(),
                            target_seconds=0.0, use_ai="never", reorder=False,
                            learning=learning, progress=progress)
    settings = result.settings
    if shape in ASPECTS:
        settings.aspect = shape
    settings.seconds_each = each
    settings.crossfade = CROSSFADE
    settings.atmosphere = "None"
    settings.music = music
    for shot in result.shots:
        shot.seconds = each
        # Faithful: the photo as it is. No colour look, no particles, no vignette or grain.
        shot.atmosphere = "None"
        shot.vignette = False
        shot.grain = 0.0
        shot.transform = "Camera move (free)"
        shot.resolved_transform = "Camera move"

    asked_seconds = max(MIN_SECONDS, min(MAX_SECONDS, length))
    actual = settings.total_seconds(result.shots)
    if abs(actual - asked_seconds) > 1.0:
        notes.append(f"{len(photos)} photos at {PER_PHOTO_RANGE[0]:g}–{PER_PHOTO_RANGE[1]:g} s "
                     f"each make {actual:.0f} s, not {asked_seconds:.0f} s.")

    ai_shots = 0
    model = result.ai_model
    if ai_level != "off":
        if model is None:
            notes.append("No AI video model is ready (start ComfyUI), so every photo gets a "
                         "camera move.")
        else:
            for index in ai_shot_indices(len(result.shots), ai_level):
                shot = result.shots[index]
                shot.transform = shot.resolved_transform = "AI video"
                shot.ai_provider, shot.ai_model = model
                shot.ai_prompt = FAITHFUL_PROMPT
                ai_shots += 1
    minutes = (ai_shots * autopilot.local_minutes_per_shot(min(each, 3.4))
               if model in autopilot.LOCAL_MODELS else 0.0)
    return QuickPlan(result, actual, ai_shots, minutes, notes)
