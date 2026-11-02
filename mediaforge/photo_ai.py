"""One photo, made to move by the AI model -- and checked before it is used.

Both photo screens (Quick clip and Photos to video) animate a shot through this, so the
same prompt and the same faithfulness check apply everywhere: a clip that changed what
is in the photo is dropped and the shot keeps a camera move instead.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import autopilot, fidelity
from .photofilm import Shot
from .providers import Capability, GenerationRequest

# What the model is asked for: the photo, alive -- never a different picture.
FAITHFUL_PROMPT = (
    "Bring this exact photo to life with gentle, natural motion of what is already in it: "
    "leaves and plants sway, water ripples, clouds drift, light shifts softly. The camera "
    "holds steady. Nothing new appears, nothing disappears, and nothing changes shape."
)


def make_renderer(context, frame_size: Callable[[], tuple[int, int]], guard: bool = True,
                  quality: str | None = None):
    """The `ai_renderer` photofilm.render calls: returns a clip, or None for a camera move."""

    def renderer(shot: Shot, index: int, output_dir: Path, progress, cancel):
        provider_key = shot.ai_provider or "catalog"
        model_id = shot.ai_model
        if not model_id:
            chosen, _ = autopilot.best_ai_model(context.registry)
            if chosen is None:
                return None
            provider_key, model_id = chosen
        provider = context.registry.get(provider_key)
        if provider is None:
            return None

        width, height = frame_size()
        aspect = "1:1" if width == height else ("9:16" if height > width else "16:9")
        options = {
            "duration": max(2, int(round(shot.seconds))),
            "duration_seconds": max(2, int(round(shot.seconds))),
            "seconds": shot.seconds,
            "aspect_ratio": aspect,
        }
        if quality:
            options["quality"] = quality
        request = GenerationRequest(
            capability=Capability.IMAGE_TO_VIDEO, model_id=model_id,
            prompt=shot.ai_prompt or FAITHFUL_PROMPT, reference_images=[shot.path],
            options=options, output_dir=output_dir,
        )
        result = provider.generate(request, progress=progress,
                                   should_cancel=cancel or (lambda: False))
        # A model that animates the scene but not the camera says so; the film then
        # adds the shot's own camera move when it conforms the clip.
        shot.ai_static_camera = (result.raw or {}).get("camera") == "static"
        clip = result.files[0] if result.files else None
        if clip is None or not guard:
            return clip

        progress(0.97, f"Photo {index + 1}: checking the clip is true to the photo")
        verdict = fidelity.check(context.locator, shot.path, Path(clip))
        if not verdict.faithful:
            shot.note = (f"AI motion not used — {verdict.reason}. "
                         "This photo has a camera move instead.")
            return None
        return clip

    return renderer
