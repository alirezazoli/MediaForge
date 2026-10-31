"""Provider contract.

A provider is any remote or local service that can generate media. The UI never imports
a concrete provider -- it asks the registry for everything that advertises a Capability
and drives it through this interface.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Protocol


class Capability(str, Enum):
    TEXT_TO_IMAGE = "text_to_image"
    IMAGE_EDIT = "image_edit"            # Nano Banana territory: edit/compose from refs
    TEXT_TO_VIDEO = "text_to_video"
    IMAGE_TO_VIDEO = "image_to_video"
    VIDEO_EXTEND = "video_extend"
    LIP_SYNC = "lip_sync"
    MOTION_TRANSFER = "motion_transfer"
    UPSCALE = "upscale"
    INTERPOLATE = "interpolate"          # frame interpolation / slow motion
    DENOISE = "denoise"
    RESTORE = "restore"                  # face/detail restoration
    MATTE = "matte"                      # background removal / rotoscoping
    SEPARATE_AUDIO = "separate_audio"    # stem splitting
    TEXT_TO_SPEECH = "text_to_speech"
    TRANSCRIBE = "transcribe"
    LLM = "llm"                          # scriptwriting / shotlisting / captions
    VIDEO_UNDERSTANDING = "video_understanding"

    @property
    def label(self) -> str:
        return _CAPABILITY_LABELS[self]


_CAPABILITY_LABELS = {
    Capability.TEXT_TO_IMAGE: "Text to image",
    Capability.IMAGE_EDIT: "Image edit / compose",
    Capability.TEXT_TO_VIDEO: "Text to video",
    Capability.IMAGE_TO_VIDEO: "Image to video",
    Capability.VIDEO_EXTEND: "Extend video",
    Capability.LIP_SYNC: "Lip sync",
    Capability.MOTION_TRANSFER: "Motion transfer",
    Capability.UPSCALE: "Upscale / enhance",
    Capability.INTERPOLATE: "Frame interpolation",
    Capability.DENOISE: "Denoise",
    Capability.RESTORE: "Restore faces / detail",
    Capability.MATTE: "Remove background",
    Capability.SEPARATE_AUDIO: "Separate audio stems",
    Capability.TEXT_TO_SPEECH: "Text to speech",
    Capability.TRANSCRIBE: "Transcribe",
    Capability.LLM: "Text / scripting",
    Capability.VIDEO_UNDERSTANDING: "Video understanding",
}


@dataclass
class ModelSpec:
    """One selectable model on a provider."""
    id: str
    label: str
    capabilities: list[Capability]
    notes: str = ""
    # Declarative option schema: name -> {type, default, choices?, min?, max?, label?}
    options: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class GenerationRequest:
    capability: Capability
    model_id: str
    prompt: str = ""
    negative_prompt: str = ""
    reference_images: list[Path] = field(default_factory=list)
    reference_video: Path | None = None
    audio_input: Path | None = None
    options: dict[str, Any] = field(default_factory=dict)
    output_dir: Path = field(default_factory=lambda: Path.cwd())
    seed: int | None = None


@dataclass
class GenerationResult:
    files: list[Path] = field(default_factory=list)
    text: str = ""
    raw: dict[str, Any] = field(default_factory=dict)
    provider: str = ""
    model_id: str = ""
    cost_note: str = ""
    elapsed_seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return bool(self.files or self.text)


class ProviderError(RuntimeError):
    """Any provider-side failure, already carrying a human-readable message."""


class ProgressSink(Protocol):
    def __call__(self, fraction: float, message: str) -> None: ...


def _noop(fraction: float, message: str) -> None:  # pragma: no cover - default sink
    return None


class Provider:
    """Base class. Subclasses declare `models` and implement `generate`."""

    key: str = ""
    name: str = "Provider"
    homepage: str = ""
    credential_slot: str = ""
    credential_help: str = ""
    models: list[ModelSpec] = []

    def __init__(self, api_key: str = "", settings: Any = None) -> None:
        self.api_key = api_key
        self.settings = settings

    # -- introspection ------------------------------------------------------------
    @property
    def configured(self) -> bool:
        return bool(self.api_key) if self.credential_slot else True

    def capabilities(self) -> set[Capability]:
        return {capability for model in self.models for capability in model.capabilities}

    def models_for(self, capability: Capability) -> list[ModelSpec]:
        return [model for model in self.models if capability in model.capabilities]

    def model(self, model_id: str) -> ModelSpec | None:
        return next((model for model in self.models if model.id == model_id), None)

    # -- work ---------------------------------------------------------------------
    def generate(
        self,
        request: GenerationRequest,
        progress: ProgressSink = _noop,
        should_cancel: Callable[[], bool] | None = None,
    ) -> GenerationResult:
        raise NotImplementedError

    # -- helpers for subclasses ---------------------------------------------------
    def _require_key(self) -> None:
        if self.credential_slot and not self.api_key:
            raise ProviderError(
                f"{self.name} needs an API key. Add it under Settings -> Credentials. "
                f"{self.credential_help}"
            )

    @staticmethod
    def _stamped_path(output_dir: Path, prefix: str, suffix: str) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        candidate = output_dir / f"{prefix}-{stamp}{suffix}"
        counter = 1
        while candidate.exists():
            candidate = output_dir / f"{prefix}-{stamp}-{counter}{suffix}"
            counter += 1
        return candidate
