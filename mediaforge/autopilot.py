"""The intelligence layer: look at the photos, decide everything, explain why.

Auto is not a hidden default -- it is a decision the app makes, writes down, and lets the
user override one field at a time. Every automatic choice produces a line of reasoning
that the UI shows, so nothing is magic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from . import learning as learning_module
from . import sceneanalysis
from .availability import model_availability
from .photofilm import PhotoFilmSettings, Shot
from .providers import Capability
from .providers.comfy_builtin import WAN22_ID, estimate_seconds, frames_for

# Preference order when Auto picks an image-to-video model. Best-looking first; the first
# one the user can actually run wins.
AI_PREFERENCE = [
    ("catalog", "seedance-2.5"),
    ("catalog", "kling-3.0"),
    ("catalog", "veo-3.1"),
    ("catalog", "seedance-2.0"),
    ("catalog", "minimax-h3"),
    ("catalog", "kling-2.5-turbo"),
    ("catalog", "hailuo-02"),
    ("catalog", "wan-2.5"),
    ("catalog", "seedance-1.0-pro"),
    ("catalog", "pixverse-v5"),
    # Runs on this PC through ComfyUI: no key, but minutes of GPU time per shot -- so
    # any connected cloud model is preferred, and Auto spends it on fewer shots.
    ("comfyui", WAN22_ID),
    ("catalog", "wan-2.2"),
    ("catalog", "ltx-2"),
]
LOCAL_MODELS = {("comfyui", WAN22_ID)}
LOCAL_AUTO_BUDGET = 3


@dataclass
class AutoDecision:
    """One decision, with its reason, so the UI can show its working."""
    field_name: str
    value: Any
    reason: str


@dataclass
class AutoPlan:
    shots: list[Shot] = field(default_factory=list)
    settings: PhotoFilmSettings | None = None
    decisions: list[AutoDecision] = field(default_factory=list)
    ai_model: tuple[str, str] | None = None
    ai_available: bool = False
    notes: list[str] = field(default_factory=list)

    def report(self) -> str:
        lines = ["What I decided, and why:", ""]
        for decision in self.decisions:
            lines.append(f"  {decision.field_name}: {decision.value}")
            lines.append(f"     {decision.reason}")
        if self.notes:
            lines += ["", *[f"  note: {note}" for note in self.notes]]
        lines += ["", "Per photo:"]
        for index, shot in enumerate(self.shots, start=1):
            analysis = shot.analysis
            scene = sceneanalysis.SCENE_LABELS.get(
                getattr(analysis, "scene", ""), "unclassified"
            ) if analysis else "not analysed"
            lines.append(
                f"  {index}. {shot.path.name}"
            )
            lines.append(
                f"     {scene} → {shot.transform}, {shot.move}, "
                f"{shot.seconds:.1f}s, atmosphere: {shot.atmosphere}"
            )
        return "\n".join(lines)


def best_ai_model(registry) -> tuple[tuple[str, str] | None, str]:
    """The best image-to-video model this user can actually run right now."""
    for provider_key, model_id in AI_PREFERENCE:
        state = model_availability(
            registry, provider_key, model_id, Capability.IMAGE_TO_VIDEO
        )
        # Only what can run unattended: a route that needs a hand-exported ComfyUI
        # workflow would fail in the middle of a render.
        if state.usable and state.unattended:
            return (provider_key, model_id), f"{model_id} — {state.reason}"
    return None, "No image-to-video model is available yet (no API key, no local ComfyUI)."


def local_minutes_per_shot(seconds: float) -> float:
    """What one shot costs on this PC with the built-in model, from measured timings."""
    return estimate_seconds("Standard", frames_for(seconds)) / 60.0


def plan(
    photos: Sequence[Path],
    registry,
    *,
    base_settings: PhotoFilmSettings | None = None,
    target_seconds: float = 0.0,
    use_ai: str = "auto",          # "auto" | "always" | "never"
    reorder: bool = False,
    analyses: Sequence[Any] | None = None,
    learning: Any = None,
    progress=lambda fraction, message: None,
) -> AutoPlan:
    """Analyses the photos and produces a complete, explained plan."""
    result = AutoPlan(settings=base_settings or PhotoFilmSettings())
    if not photos:
        return result

    if analyses is None:
        analyses = []
        for index, path in enumerate(photos):
            progress(index / max(1, len(photos)) * 0.6, f"Looking at photo {index + 1}")
            analyses.append(sceneanalysis.analyse(path))
    analyses = list(analyses)

    # Bend the fresh recommendations toward habits the user has actually shown.
    learned: list[Any] = []
    for analysis in analyses:
        learned += learning_module.apply_to_analysis(learning, analysis)
    for sentence in dict.fromkeys(entry.sentence for entry in learned):
        result.decisions.append(AutoDecision("Learned from you", "applied", sentence))

    order = list(range(len(photos)))
    if reorder:
        order = sceneanalysis.suggest_order(analyses)
        result.decisions.append(AutoDecision(
            "Order", "reordered",
            "Wide and aerial shots first, interiors and details next, night last — "
            "it reads as an arc rather than a shuffle.",
        ))

    # -- which AI model, if any ---------------------------------------------------------
    model, model_reason = best_ai_model(registry)
    result.ai_model = model
    result.ai_available = model is not None
    if use_ai == "never":
        result.decisions.append(AutoDecision(
            "AI video", "off", "You asked for camera moves only, so nothing is sent anywhere."
        ))
    elif model is None:
        result.decisions.append(AutoDecision(
            "AI video", "unavailable",
            model_reason + " Every shot uses a free camera move instead.",
        ))
    else:
        result.decisions.append(AutoDecision("AI video model", model[1], model_reason))

    # -- portrait or landscape ----------------------------------------------------------
    settings = result.settings
    portrait_count = sum(1 for entry in analyses if entry.is_portrait)
    if portrait_count > len(analyses) * 0.6:
        settings.aspect = "9:16 vertical"
        result.decisions.append(AutoDecision(
            "Shape", "9:16 vertical",
            f"{portrait_count} of {len(analyses)} photos are portrait, so a vertical cut "
            f"crops least.",
        ))
    else:
        settings.aspect = "16:9 landscape"
        result.decisions.append(AutoDecision(
            "Shape", "16:9 landscape",
            f"Most photos are landscape ({len(analyses) - portrait_count} of "
            f"{len(analyses)}).",
        ))

    mixed = portrait_count not in (0, len(analyses))
    if mixed:
        settings.fit = "Fit whole photo (blurred edges)"
        result.decisions.append(AutoDecision(
            "Framing", "blurred edges",
            "The photos are a mix of portrait and landscape, so cropping to fill would "
            "cut heads and rooflines. A blurred backdrop keeps every frame whole.",
        ))
    else:
        settings.fit = "Fill the frame (crop edges)"

    # -- pacing ---------------------------------------------------------------------------
    suggested = [entry.suggested_seconds for entry in analyses]
    average = sum(suggested) / len(suggested)
    if target_seconds > 0:
        per_photo = max(1.5, target_seconds / len(photos))
        settings.seconds_each = round(per_photo, 1)
        result.decisions.append(AutoDecision(
            "Pace", f"{settings.seconds_each:.1f}s per photo",
            f"You asked for about {target_seconds:.0f}s total across {len(photos)} photos.",
        ))
    else:
        learned_pace = (learning.numeric_preference("seconds_each")
                        if learning is not None and learning.enabled else None)
        if learned_pace:
            # Meet the user's habitual pace halfway rather than overriding the photos.
            settings.seconds_each = round((average + learned_pace) / 2, 1)
            result.decisions.append(AutoDecision(
                "Pace", f"{settings.seconds_each:.1f}s per photo",
                f"These photos suggest {average:.1f}s, and you usually settle around "
                f"{learned_pace:.1f}s — this splits the difference.",
            ))
        else:
            settings.seconds_each = round(average, 1)
            result.decisions.append(AutoDecision(
                "Pace", f"{settings.seconds_each:.1f}s per photo",
                "Busy frames need longer to read than simple ones; this is the average of "
                "what each photo wants.",
            ))
    settings.crossfade = round(min(settings.seconds_each * 0.25, 1.0), 2)

    night_share = sum(1 for entry in analyses if entry.is_night) / len(analyses)
    if night_share > 0.5:
        settings.fade_in, settings.fade_out = 1.0, 1.6
        result.decisions.append(AutoDecision(
            "Open and close", "slow fades",
            "Mostly night shots — a slower fade from black suits the mood.",
        ))

    # -- per photo -------------------------------------------------------------------------
    shots: list[Shot] = []
    local = model in LOCAL_MODELS
    ai_budget = _ai_budget(len(photos), use_ai, local)
    ai_used = 0

    for position, source_index in enumerate(order):
        path, analysis = photos[source_index], analyses[source_index]
        shot = Shot(
            path=path,
            seconds=settings.seconds_each,
            move=analysis.move,
            motion_strength=analysis.motion_strength,
            atmosphere=analysis.atmosphere,
            atmosphere_intensity=0.5,
            analysis=analysis,
            ai_prompt=analysis.ai_prompt,
        )

        wants_ai = False
        if use_ai == "always" and model is not None:
            wants_ai = True
        elif use_ai == "auto" and model is not None and ai_used < ai_budget:
            # Real 3D movement earns its cost on wide exteriors and aerials, where a 2D
            # pan looks obviously flat. Interiors and details survive a camera move fine.
            wants_ai = analysis.scene in ("exterior_day", "exterior_night",
                                          "exterior_golden", "aerial")

        if wants_ai:
            shot.transform = "AI video"
            shot.resolved_transform = "AI video"
            shot.ai_provider, shot.ai_model = model
            ai_used += 1
        else:
            shot.transform = "Camera move (free)"
            shot.resolved_transform = "Camera move"

        shots.append(shot)

    varied = diversify_moves(shots)
    if varied:
        result.decisions.append(AutoDecision(
            "Variety", f"{varied} shot{'s' if varied != 1 else ''} changed",
            "Several photos independently wanted the same move. Repeating one move down "
            "the whole film reads as a slideshow, so neighbouring shots were given their "
            "next-best move instead.",
        ))

    result.shots = shots
    if model is not None and use_ai != "never":
        result.decisions.append(AutoDecision(
            "AI usage", f"{ai_used} of {len(shots)} shots",
            ("Wide exteriors get generated motion on this PC with Wan 2.2 — about "
             f"{local_minutes_per_shot(settings.seconds_each):.0f} min per shot, so Auto uses "
             f"it on at most {LOCAL_AUTO_BUDGET}. The rest keep a camera move."
             if local else
             "Wide exteriors and aerials get real 3D movement, where a flat pan would show. "
             "Interiors and close details keep the camera move — the difference is not "
             "worth the cost there.")
            if use_ai == "auto" else "Every shot, as you asked.",
        ))

    atmospheres = {shot.atmosphere for shot in shots if shot.atmosphere != "None"}
    if atmospheres:
        result.decisions.append(AutoDecision(
            "Atmosphere", ", ".join(sorted(atmospheres)),
            "Chosen per photo from its light: glow for night, haze for golden hour, "
            "bloom for warm interiors.",
        ))

    return result


def diversify_moves(shots: list[Shot]) -> int:
    """Breaks runs of identical camera moves. Returns how many shots were changed.

    Photos of the same kind honestly want the same move, so a set of five interiors would
    all get 'Zoom in' and the film would look like one shot repeated. Each shot keeps its
    first choice unless the shot before it already took that move, in which case it walks
    down its own ranked alternatives.
    """
    changed = 0
    for index in range(1, len(shots)):
        shot, previous = shots[index], shots[index - 1]
        if shot.move != previous.move:
            continue

        analysis = shot.analysis
        alternatives = list(getattr(analysis, "move_alternatives", []) or [])
        # A generic fallback keeps this working for photos that were never analysed.
        alternatives += ["Zoom in", "Pan right", "Zoom out", "Pan left",
                         "Zoom in + pan up", "Pan down"]

        two_back = shots[index - 2].move if index >= 2 else ""
        replacement = next(
            (candidate for candidate in alternatives
             if candidate != previous.move and candidate != two_back),
            "",
        )
        if not replacement:
            continue

        shot.move = replacement
        shot.note = (shot.note + " " if shot.note else "") + (
            f"Move changed to {replacement} so it does not repeat the shot before it."
        )
        changed += 1
    return changed


def _ai_budget(count: int, use_ai: str, local: bool = False) -> int:
    """How many shots Auto is willing to spend money -- or GPU minutes -- on."""
    if use_ai == "always":
        return count
    if use_ai == "never":
        return 0
    # Enough to carry the film without turning a 20-photo set into a large bill, or,
    # on this PC, into an hour of rendering.
    return max(1, min(count, LOCAL_AUTO_BUDGET if local else 6))


def apply_global_overrides(
    shots: list[Shot], *, move: str = "", atmosphere: str = "", transform: str = "",
    seconds: float = 0.0, strength: float = -1.0,
) -> None:
    """Applies a non-Auto global choice across every shot, leaving Auto ones analysed."""
    for index, shot in enumerate(shots):
        if move and move != "Auto":
            shot.move = move
        if atmosphere and atmosphere != "Auto":
            shot.atmosphere = atmosphere
        if transform and transform != "Auto":
            shot.transform = transform
            shot.resolved_transform = ""
        if seconds > 0:
            shot.seconds = seconds
        if strength >= 0:
            shot.motion_strength = strength
