"""Supporting providers: Claude for scripting, ElevenLabs for voice, and a generic
OpenAI-compatible client that covers OpenAI itself plus every self-hosted clone
(LM Studio, Ollama, vLLM, Groq, Together, OpenRouter).
"""

from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Any

from .base import (
    Capability, GenerationRequest, GenerationResult, ModelSpec, Provider, ProviderError,
)
from .http import download, request_json


class AnthropicProvider(Provider):
    key = "anthropic"
    name = "Anthropic Claude (scripting & direction)"
    homepage = "https://console.anthropic.com/settings/keys"
    credential_slot = "anthropic"
    credential_help = "Create a key at console.anthropic.com/settings/keys."

    ROOT = "https://api.anthropic.com/v1"

    models = [
        ModelSpec(
            id="claude-opus-5",
            label="Claude Opus 5 - best scripts and shot direction",
            capabilities=[Capability.LLM],
            options={
                "temperature": {"type": "float", "default": 1.0, "min": 0.0, "max": 1.0},
                "max_tokens": {"type": "int", "default": 4096, "min": 256, "max": 32000},
            },
        ),
        ModelSpec(
            id="claude-sonnet-5",
            label="Claude Sonnet 5 - fast, cheap drafting",
            capabilities=[Capability.LLM],
            options={
                "temperature": {"type": "float", "default": 1.0, "min": 0.0, "max": 1.0},
                "max_tokens": {"type": "int", "default": 4096, "min": 256, "max": 32000},
            },
        ),
    ]

    def _headers(self) -> dict[str, str]:
        self._require_key()
        return {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

    def generate(self, request, progress=lambda f, m: None, should_cancel=None) -> GenerationResult:
        started = time.time()
        progress(0.2, "Calling Claude")

        content: list[dict[str, Any]] = []
        for image in request.reference_images[:8]:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png" if image.suffix.lower() == ".png" else "image/jpeg",
                    "data": base64.b64encode(image.read_bytes()).decode("ascii"),
                },
            })
        content.append({"type": "text", "text": request.prompt})

        payload = request_json(
            "POST", f"{self.ROOT}/messages", headers=self._headers(),
            json_body={
                "model": request.model_id,
                "max_tokens": int(request.options.get("max_tokens", 4096)),
                "temperature": float(request.options.get("temperature", 1.0)),
                "messages": [{"role": "user", "content": content}],
            },
            proxy=getattr(self.settings, "proxy_url", "") or "",
            timeout=300,
        )
        text = "".join(
            block.get("text", "") for block in payload.get("content", [])
            if block.get("type") == "text"
        )
        progress(1.0, "Done")
        return GenerationResult(
            text=text, raw=payload, provider=self.key, model_id=request.model_id,
            elapsed_seconds=time.time() - started,
        )


class ElevenLabsProvider(Provider):
    key = "elevenlabs"
    name = "ElevenLabs (voiceover & SFX)"
    homepage = "https://elevenlabs.io/app/settings/api-keys"
    credential_slot = "elevenlabs"
    credential_help = "Create a key at elevenlabs.io/app/settings/api-keys."

    ROOT = "https://api.elevenlabs.io/v1"

    models = [
        ModelSpec(
            id="eleven_multilingual_v2",
            label="Multilingual v2 - narration",
            capabilities=[Capability.TEXT_TO_SPEECH],
            notes="Paste a voice id from your ElevenLabs voice library.",
            options={
                "voice_id": {"type": "str", "default": "21m00Tcm4TlvDq8ikWAM",
                             "label": "Voice id"},
                "stability": {"type": "float", "default": 0.5, "min": 0.0, "max": 1.0},
                "similarity_boost": {"type": "float", "default": 0.75, "min": 0.0, "max": 1.0},
                "speed": {"type": "float", "default": 1.0, "min": 0.7, "max": 1.2},
            },
        ),
        ModelSpec(
            id="eleven_turbo_v2_5",
            label="Turbo v2.5 - fast, low latency",
            capabilities=[Capability.TEXT_TO_SPEECH],
            options={
                "voice_id": {"type": "str", "default": "21m00Tcm4TlvDq8ikWAM",
                             "label": "Voice id"},
                "stability": {"type": "float", "default": 0.5, "min": 0.0, "max": 1.0},
            },
        ),
        ModelSpec(
            id="scribe_v1",
            label="Scribe v1 - transcription with word timings",
            capabilities=[Capability.TRANSCRIBE],
            notes="Produces word-level timestamps, which the Subtitles panel turns into SRT.",
            options={"language_code": {"type": "str", "default": "", "label": "Language (blank = auto)"}},
        ),
    ]

    def generate(self, request, progress=lambda f, m: None, should_cancel=None) -> GenerationResult:
        self._require_key()
        started = time.time()
        if request.capability == Capability.TRANSCRIBE:
            result = self._transcribe(request, progress)
        else:
            result = self._speak(request, progress)
        result.provider, result.model_id = self.key, request.model_id
        result.elapsed_seconds = time.time() - started
        return result

    def _speak(self, request: GenerationRequest, progress) -> GenerationResult:
        import requests

        progress(0.3, "Synthesising voice")
        voice_id = request.options.get("voice_id") or "21m00Tcm4TlvDq8ikWAM"
        response = requests.post(
            f"{self.ROOT}/text-to-speech/{voice_id}",
            headers={"xi-api-key": self.api_key, "Content-Type": "application/json"},
            json={
                "text": request.prompt,
                "model_id": request.model_id,
                "voice_settings": {
                    "stability": float(request.options.get("stability", 0.5)),
                    "similarity_boost": float(request.options.get("similarity_boost", 0.75)),
                    "speed": float(request.options.get("speed", 1.0)),
                },
            },
            timeout=300,
        )
        if response.status_code >= 400:
            raise ProviderError(f"ElevenLabs error {response.status_code}: {response.text[:400]}")
        destination = self._stamped_path(request.output_dir, "voiceover", ".mp3")
        destination.write_bytes(response.content)
        progress(1.0, "Done")
        return GenerationResult(files=[destination])

    def _transcribe(self, request: GenerationRequest, progress) -> GenerationResult:
        import requests

        source = request.audio_input or request.reference_video
        if not source or not source.exists():
            raise ProviderError("Select an audio or video file to transcribe.")
        progress(0.3, "Uploading for transcription")

        data = {"model_id": request.model_id, "timestamps_granularity": "word"}
        if request.options.get("language_code"):
            data["language_code"] = request.options["language_code"]
        with source.open("rb") as handle:
            response = requests.post(
                f"{self.ROOT}/speech-to-text",
                headers={"xi-api-key": self.api_key},
                data=data, files={"file": (source.name, handle)}, timeout=1200,
            )
        if response.status_code >= 400:
            raise ProviderError(f"ElevenLabs error {response.status_code}: {response.text[:400]}")
        payload = response.json()
        progress(1.0, "Done")
        return GenerationResult(text=payload.get("text", ""), raw=payload)


class OpenAICompatibleProvider(Provider):
    """Works against api.openai.com or any base_url that speaks the same shapes."""

    key = "openai_compatible"
    name = "OpenAI-compatible endpoint"
    homepage = "https://platform.openai.com/api-keys"
    credential_slot = "openai_compatible"
    credential_help = (
        "Set the base URL in Settings. Works with OpenAI, OpenRouter, Groq, Together, "
        "vLLM, LM Studio and Ollama (http://localhost:11434/v1)."
    )

    models = [
        ModelSpec(
            id="gpt-image-1", label="gpt-image-1 - image generation/edit",
            capabilities=[Capability.TEXT_TO_IMAGE, Capability.IMAGE_EDIT],
            options={
                "size": {"type": "choice",
                         "choices": ["1024x1024", "1536x1024", "1024x1536", "auto"],
                         "default": "1536x1024"},
                "n": {"type": "int", "default": 1, "min": 1, "max": 4},
                "quality": {"type": "choice", "choices": ["low", "medium", "high", "auto"],
                            "default": "high"},
            },
        ),
        ModelSpec(
            id="whisper-1", label="whisper-1 - transcription",
            capabilities=[Capability.TRANSCRIBE],
            options={"language": {"type": "str", "default": "", "label": "Language (blank = auto)"}},
        ),
        ModelSpec(
            id="gpt-4o-mini-tts", label="gpt-4o-mini-tts - voiceover",
            capabilities=[Capability.TEXT_TO_SPEECH],
            options={
                "voice": {"type": "choice",
                          "choices": ["alloy", "echo", "fable", "onyx", "nova", "shimmer"],
                          "default": "alloy"},
            },
        ),
        ModelSpec(
            id="gpt-4.1", label="gpt-4.1 - text",
            capabilities=[Capability.LLM],
            options={"temperature": {"type": "float", "default": 0.8, "min": 0.0, "max": 2.0}},
        ),
    ]

    @property
    def base_url(self) -> str:
        return (getattr(self.settings, "openai_base_url", "")
                or "https://api.openai.com/v1").rstrip("/")

    def _headers(self) -> dict[str, str]:
        self._require_key()
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def generate(self, request, progress=lambda f, m: None, should_cancel=None) -> GenerationResult:
        started = time.time()
        capability = request.capability

        if capability in (Capability.TEXT_TO_IMAGE, Capability.IMAGE_EDIT):
            result = self._image(request, progress)
        elif capability == Capability.TRANSCRIBE:
            result = self._transcribe(request, progress)
        elif capability == Capability.TEXT_TO_SPEECH:
            result = self._speak(request, progress)
        else:
            result = self._chat(request, progress)

        result.provider, result.model_id = self.key, request.model_id
        result.elapsed_seconds = time.time() - started
        return result

    def _image(self, request: GenerationRequest, progress) -> GenerationResult:
        import requests

        progress(0.2, "Generating image")
        if request.reference_images:
            files = [
                ("image[]", (path.name, path.open("rb"), "image/png"))
                for path in request.reference_images[:4]
            ]
            try:
                response = requests.post(
                    f"{self.base_url}/images/edits",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    data={"model": request.model_id, "prompt": request.prompt,
                          "size": request.options.get("size", "auto"),
                          "n": int(request.options.get("n", 1))},
                    files=files, timeout=600,
                )
            finally:
                for _, (_, handle, _) in files:
                    handle.close()
            if response.status_code >= 400:
                raise ProviderError(f"Image edit failed {response.status_code}: {response.text[:400]}")
            payload = response.json()
        else:
            payload = request_json(
                "POST", f"{self.base_url}/images/generations", headers=self._headers(),
                json_body={
                    "model": request.model_id, "prompt": request.prompt,
                    "n": int(request.options.get("n", 1)),
                    "size": request.options.get("size", "1024x1024"),
                    "quality": request.options.get("quality", "high"),
                },
                timeout=600,
            )

        saved: list[Path] = []
        for entry in payload.get("data", []):
            destination = self._stamped_path(request.output_dir, "openai", ".png")
            if entry.get("b64_json"):
                destination.write_bytes(base64.b64decode(entry["b64_json"]))
            elif entry.get("url"):
                download(entry["url"], destination)
            else:
                continue
            saved.append(destination)
        if not saved:
            raise ProviderError(f"No image returned: {str(payload)[:400]}")
        progress(1.0, "Done")
        return GenerationResult(files=saved, raw=payload)

    def _transcribe(self, request: GenerationRequest, progress) -> GenerationResult:
        import requests

        source = request.audio_input or request.reference_video
        if not source or not source.exists():
            raise ProviderError("Select an audio or video file to transcribe.")
        progress(0.3, "Uploading audio")
        data = {"model": request.model_id, "response_format": "verbose_json",
                "timestamp_granularities[]": "segment"}
        if request.options.get("language"):
            data["language"] = request.options["language"]
        with source.open("rb") as handle:
            response = requests.post(
                f"{self.base_url}/audio/transcriptions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                data=data, files={"file": (source.name, handle)}, timeout=1800,
            )
        if response.status_code >= 400:
            raise ProviderError(f"Transcription failed {response.status_code}: {response.text[:400]}")
        payload = response.json()
        progress(1.0, "Done")
        return GenerationResult(text=payload.get("text", ""), raw=payload)

    def _speak(self, request: GenerationRequest, progress) -> GenerationResult:
        import requests

        progress(0.3, "Synthesising voice")
        response = requests.post(
            f"{self.base_url}/audio/speech", headers=self._headers(),
            json={"model": request.model_id, "input": request.prompt,
                  "voice": request.options.get("voice", "alloy"), "response_format": "mp3"},
            timeout=600,
        )
        if response.status_code >= 400:
            raise ProviderError(f"TTS failed {response.status_code}: {response.text[:400]}")
        destination = self._stamped_path(request.output_dir, "voiceover", ".mp3")
        destination.write_bytes(response.content)
        progress(1.0, "Done")
        return GenerationResult(files=[destination])

    def _chat(self, request: GenerationRequest, progress) -> GenerationResult:
        progress(0.2, "Calling model")
        content: list[dict[str, Any]] = [{"type": "text", "text": request.prompt}]
        for image in request.reference_images[:6]:
            encoded = base64.b64encode(image.read_bytes()).decode("ascii")
            content.append({"type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{encoded}"}})
        payload = request_json(
            "POST", f"{self.base_url}/chat/completions", headers=self._headers(),
            json_body={
                "model": request.model_id,
                "messages": [{"role": "user", "content": content}],
                "temperature": float(request.options.get("temperature", 0.8)),
            },
            timeout=600,
        )
        text = (payload.get("choices") or [{}])[0].get("message", {}).get("content", "")
        progress(1.0, "Done")
        return GenerationResult(text=text or "", raw=payload)
