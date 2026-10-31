"""The Director agent: a brief in, a finished cut out.

Pipeline
    1. An LLM turns the brief into a JSON shotlist (prompt, duration, camera, narration).
    2. Optionally a still is generated per shot to lock composition and character identity.
    3. Each shot is rendered as video (image-to-video when a still exists, else text-to-video).
    4. Optional narration is synthesised and mixed under the cut.
    5. ffmpeg joins everything, with crossfades, into renders/.

Every step is skippable and every intermediate lands in the project, so a failed step
never costs the work before it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import ffmpeg_tools
from .jobs import JobContext
from .project import Project
from .providers import Capability, GenerationRequest, Registry

SHOTLIST_INSTRUCTIONS = """You are a video director. Turn the brief below into a shotlist.

Reply with ONLY a JSON object, no markdown fence, in exactly this shape:
{
  "title": "short title",
  "shots": [
    {
      "id": 1,
      "summary": "one line of what happens",
      "image_prompt": "detailed still-image prompt: subject, setting, lighting, lens, mood",
      "video_prompt": "detailed motion prompt: camera move, subject action, pacing",
      "duration_seconds": 5,
      "narration": "the voiceover line for this shot, or empty string"
    }
  ]
}

Rules:
- Produce exactly {shot_count} shots.
- Keep total runtime near {total_seconds} seconds.
- Carry a consistent visual style across shots: repeat the key style words in every prompt.
- Prompts must be self-contained; a generator sees one shot at a time with no memory.
- Narration lines must be speakable in the shot's duration (about 2.5 words per second).

BRIEF:
{brief}

STYLE NOTES: {style}
"""


@dataclass
class ShotPlan:
    id: int
    summary: str = ""
    image_prompt: str = ""
    video_prompt: str = ""
    duration_seconds: float = 5.0
    narration: str = ""
    still: Path | None = None
    clip: Path | None = None
    error: str = ""


@dataclass
class DirectorConfig:
    brief: str
    style: str = "cinematic, natural light, shallow depth of field, 35mm"
    shot_count: int = 4
    total_seconds: int = 20
    aspect_ratio: str = "16:9"

    llm_provider: str = "anthropic"
    llm_model: str = "claude-opus-5"

    generate_stills: bool = True
    image_provider: str = "google"
    image_model: str = "gemini-2.5-flash-image"

    generate_clips: bool = True
    video_provider: str = "google"
    video_model: str = "veo-3.1-generate-preview"

    generate_narration: bool = False
    tts_provider: str = "elevenlabs"
    tts_model: str = "eleven_multilingual_v2"
    tts_options: dict[str, Any] = field(default_factory=dict)

    assemble: bool = True
    crossfade: float = 0.5
    output_width: int = 1920
    output_height: int = 1080
    fps: int = 30

    # Optional: reference stills that lock a character or product across every shot.
    identity_references: list[Path] = field(default_factory=list)


@dataclass
class DirectorResult:
    title: str = ""
    shots: list[ShotPlan] = field(default_factory=list)
    final_video: Path | None = None
    narration_audio: Path | None = None
    shotlist_json: str = ""
    warnings: list[str] = field(default_factory=list)


def _extract_json(text: str) -> dict:
    """LLMs still wrap JSON in prose or fences often enough to need this."""
    candidate = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)```", candidate, re.DOTALL)
    if fenced:
        candidate = fenced.group(1).strip()
    start, end = candidate.find("{"), candidate.rfind("}")
    if start != -1 and end > start:
        candidate = candidate[start:end + 1]
    return json.loads(candidate)


class Director:
    def __init__(self, registry: Registry, project: Project, locator: ffmpeg_tools.FfmpegLocator) -> None:
        self.registry = registry
        self.project = project
        self.locator = locator

    # -- step 1: plan ---------------------------------------------------------------
    def plan(self, config: DirectorConfig, context: JobContext) -> tuple[list[ShotPlan], str, str]:
        context.progress(0.02, "Writing the shotlist")
        provider, _ = self.registry.resolve(config.llm_provider, config.llm_model)
        prompt = (
            SHOTLIST_INSTRUCTIONS
            .replace("{shot_count}", str(config.shot_count))
            .replace("{total_seconds}", str(config.total_seconds))
            .replace("{brief}", config.brief)
            .replace("{style}", config.style)
        )
        response = provider.generate(
            GenerationRequest(
                capability=Capability.LLM, model_id=config.llm_model, prompt=prompt,
                output_dir=self.project.media_dir, options={"temperature": 0.9, "max_tokens": 6000},
            ),
            progress=lambda fraction, message: context.progress(0.02 + fraction * 0.08, message),
            should_cancel=lambda: context.cancelled,
        )
        try:
            payload = _extract_json(response.text)
        except (json.JSONDecodeError, ValueError) as error:
            raise RuntimeError(
                f"The model did not return a usable shotlist ({error}). "
                f"First 400 characters of its reply:\n{response.text[:400]}"
            ) from error

        shots = [
            ShotPlan(
                id=int(entry.get("id", index + 1)),
                summary=str(entry.get("summary", "")),
                image_prompt=str(entry.get("image_prompt") or entry.get("video_prompt", "")),
                video_prompt=str(entry.get("video_prompt") or entry.get("image_prompt", "")),
                duration_seconds=float(entry.get("duration_seconds", 5) or 5),
                narration=str(entry.get("narration", "")),
            )
            for index, entry in enumerate(payload.get("shots", []))
        ]
        if not shots:
            raise RuntimeError("The shotlist came back empty.")
        return shots, str(payload.get("title", "Untitled")), response.text

    # -- full run --------------------------------------------------------------------
    def run(self, config: DirectorConfig, context: JobContext) -> DirectorResult:
        shots, title, raw = self.plan(config, context)
        result = DirectorResult(title=title, shots=shots, shotlist_json=raw)
        context.raise_if_cancelled()

        total_steps = max(1, len(shots) * ((1 if config.generate_stills else 0)
                                           + (1 if config.generate_clips else 0)))
        completed = 0

        def bump(message: str) -> None:
            context.progress(0.10 + 0.75 * (completed / total_steps), message)

        for shot in shots:
            context.raise_if_cancelled()

            if config.generate_stills:
                try:
                    bump(f"Shot {shot.id}: still")
                    provider, _ = self.registry.resolve(config.image_provider, config.image_model)
                    response = provider.generate(
                        GenerationRequest(
                            capability=(Capability.IMAGE_EDIT if config.identity_references
                                        else Capability.TEXT_TO_IMAGE),
                            model_id=config.image_model,
                            prompt=f"{shot.image_prompt}. Style: {config.style}",
                            reference_images=list(config.identity_references),
                            options={"aspect_ratio": config.aspect_ratio, "candidate_count": 1},
                            output_dir=self.project.media_dir,
                        ),
                        should_cancel=lambda: context.cancelled,
                    )
                    if response.files:
                        shot.still = response.files[0]
                        self._catalogue(shot.still, "image", f"{title} - shot {shot.id} still")
                except Exception as error:
                    shot.error = f"still: {error}"
                    result.warnings.append(f"Shot {shot.id} still failed: {error}")
                completed += 1

            if config.generate_clips:
                try:
                    bump(f"Shot {shot.id}: video")
                    provider, _ = self.registry.resolve(config.video_provider, config.video_model)
                    capability = (Capability.IMAGE_TO_VIDEO if shot.still
                                  else Capability.TEXT_TO_VIDEO)
                    if capability not in provider.capabilities():
                        capability = Capability.TEXT_TO_VIDEO
                    response = provider.generate(
                        GenerationRequest(
                            capability=capability,
                            model_id=config.video_model,
                            prompt=f"{shot.video_prompt}. Style: {config.style}",
                            reference_images=[shot.still] if shot.still else [],
                            options={
                                "aspect_ratio": config.aspect_ratio,
                                "ratio": config.aspect_ratio,
                                "duration_seconds": int(shot.duration_seconds),
                                "duration": int(shot.duration_seconds),
                            },
                            output_dir=self.project.media_dir,
                        ),
                        progress=lambda fraction, message, shot_id=shot.id: context.progress(
                            0.10 + 0.75 * (completed / total_steps),
                            f"Shot {shot_id}: {message}",
                        ),
                        should_cancel=lambda: context.cancelled,
                    )
                    if response.files:
                        shot.clip = response.files[0]
                        self._catalogue(shot.clip, "video", f"{title} - shot {shot.id}")
                except Exception as error:
                    shot.error = f"{shot.error} video: {error}".strip()
                    result.warnings.append(f"Shot {shot.id} video failed: {error}")
                completed += 1

        context.raise_if_cancelled()

        if config.generate_narration:
            narration = " ".join(shot.narration for shot in shots if shot.narration).strip()
            if narration:
                try:
                    context.progress(0.88, "Recording narration")
                    provider, _ = self.registry.resolve(config.tts_provider, config.tts_model)
                    response = provider.generate(
                        GenerationRequest(
                            capability=Capability.TEXT_TO_SPEECH, model_id=config.tts_model,
                            prompt=narration, options=dict(config.tts_options),
                            output_dir=self.project.media_dir,
                        ),
                        should_cancel=lambda: context.cancelled,
                    )
                    if response.files:
                        result.narration_audio = response.files[0]
                        self._catalogue(result.narration_audio, "audio", f"{title} - narration")
                except Exception as error:
                    result.warnings.append(f"Narration failed: {error}")

        clips = [shot.clip for shot in shots if shot.clip and shot.clip.exists()]
        if config.assemble and clips and self.locator.available:
            try:
                context.progress(0.92, "Assembling the cut")
                result.final_video = self._assemble(config, clips, title, result, context)
            except Exception as error:
                result.warnings.append(f"Assembly failed: {error}")

        context.progress(1.0, "Director finished")
        return result

    # -- helpers -----------------------------------------------------------------------
    def _assemble(
        self, config: DirectorConfig, clips: list[Path], title: str,
        result: DirectorResult, context: JobContext,
    ) -> Path:
        durations = []
        for clip in clips:
            try:
                durations.append(ffmpeg_tools.probe(self.locator, clip).duration)
            except Exception:
                durations.append(5.0)

        safe_title = re.sub(r"[^\w\- ]", "", title).strip().replace(" ", "_") or "cut"
        silent = self.project.renders_dir / f"{safe_title}_silent.mp4"
        command = ffmpeg_tools.concat(
            self.locator, clips, silent, width=config.output_width, height=config.output_height,
            fps=config.fps, crossfade=config.crossfade, durations=durations,
        )
        code, log = ffmpeg_tools.run_with_progress(
            command,
            on_progress=lambda fraction, label: context.progress(0.92 + fraction * 0.05,
                                                                 f"Assembling {label}"),
            should_cancel=lambda: context.cancelled,
        )
        if code != 0:
            raise RuntimeError(f"ffmpeg concat failed (exit {code}):\n{log}")

        final = silent
        if result.narration_audio and result.narration_audio.exists():
            final = self.project.renders_dir / f"{safe_title}.mp4"
            mux = ffmpeg_tools.mux_audio(
                self.locator, silent, result.narration_audio, final,
                video_duration=command.total_duration, keep_original=False,
            )
            code, log = ffmpeg_tools.run_with_progress(mux)
            if code != 0:
                raise RuntimeError(f"ffmpeg audio mux failed (exit {code}):\n{log}")

        self._catalogue(final, "video", f"{title} - final cut")
        return final

    def _catalogue(self, path: Path, kind: str, label: str) -> None:
        info = None
        if self.locator.available:
            try:
                info = ffmpeg_tools.probe(self.locator, path)
            except Exception:
                info = None
        thumbnail = ""
        if kind in ("video", "image") and self.locator.available:
            candidate = self.project.thumbs_dir / f"{path.stem}.jpg"
            if ffmpeg_tools.make_thumbnail(self.locator, path, candidate):
                thumbnail = str(candidate)
        self.project.add_asset(
            path, kind, label=label,
            duration=info.duration if info else 0.0,
            width=info.width if info else 0, height=info.height if info else 0,
            fps=info.fps if info else 0.0,
            size_bytes=path.stat().st_size if path.exists() else 0,
            thumbnail=thumbnail, source="generated",
        )
