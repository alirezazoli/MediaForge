"""Seedance, Qwen-Omni, fal.ai, Replicate and ComfyUI providers.

fal.ai and Replicate are deliberately generic: they are the door to the long tail of
open-source models on GitHub (Wan, LTX-Video, HunyuanVideo, Flux, SDXL, CogVideoX,
Real-ESRGAN, Whisper...). Users add a model id in Settings and it becomes a first-class
entry in the Generate panel -- no code change needed.
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from pathlib import Path
from typing import Any

from . import comfy_builtin, comfy_local
from .base import (
    Capability, GenerationRequest, GenerationResult, ModelSpec, Provider, ProviderError,
)
from .http import download, poll_until, request_json

ASPECTS = ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"]


def data_uri(path: Path) -> str:
    mime = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".webp": "image/webp", ".mp4": "video/mp4", ".wav": "audio/wav", ".mp3": "audio/mpeg",
    }.get(path.suffix.lower(), "application/octet-stream")
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def _suffix_from_url(url: str, fallback: str) -> str:
    tail = url.split("?")[0].rsplit(".", 1)
    return f".{tail[-1]}" if len(tail) == 2 and len(tail[-1]) <= 5 else fallback


# ======================================================================================
# ByteDance Seedance via Volcengine Ark / BytePlus ModelArk
# ======================================================================================

class SeedanceProvider(Provider):
    key = "seedance"
    name = "ByteDance Seedance (Volcengine Ark)"
    homepage = "https://console.volcengine.com/ark"
    credential_slot = "volcengine"
    credential_help = (
        "Create an Ark API key at console.volcengine.com/ark (mainland China) or "
        "console.byteplus.com/ark (international), then set the matching region below."
    )

    REGIONS = {
        "china": "https://ark.cn-beijing.volces.com/api/v3",
        "international": "https://ark.ap-southeast.bytepluses.com/api/v3",
    }

    models = [
        ModelSpec(
            id="doubao-seedance-1-0-pro-250528",
            label="Seedance 1.0 Pro - multi-shot video",
            capabilities=[Capability.TEXT_TO_VIDEO, Capability.IMAGE_TO_VIDEO],
            notes="Strong at multi-shot narrative continuity and camera motion. "
                  "Text commands (--ratio, --dur, --fps) are appended to the prompt.",
            options={
                "ratio": {"type": "choice", "choices": ASPECTS + ["adaptive"], "default": "16:9"},
                "duration": {"type": "int", "default": 5, "min": 3, "max": 12,
                             "label": "Seconds"},
                "resolution": {"type": "choice", "choices": ["480p", "720p", "1080p"],
                               "default": "720p"},
                "camerafixed": {"type": "bool", "default": False, "label": "Lock camera"},
                "watermark": {"type": "bool", "default": False},
            },
        ),
        ModelSpec(
            id="doubao-seedance-1-0-lite-t2v-250428",
            label="Seedance 1.0 Lite - text to video",
            capabilities=[Capability.TEXT_TO_VIDEO],
            options={
                "ratio": {"type": "choice", "choices": ASPECTS, "default": "16:9"},
                "duration": {"type": "int", "default": 5, "min": 3, "max": 12},
                "resolution": {"type": "choice", "choices": ["480p", "720p"], "default": "720p"},
            },
        ),
        ModelSpec(
            id="doubao-seedance-1-0-lite-i2v-250428",
            label="Seedance 1.0 Lite - image to video",
            capabilities=[Capability.IMAGE_TO_VIDEO],
            notes="Supply a first frame, and optionally a last frame as the second reference.",
            options={
                "ratio": {"type": "choice", "choices": ASPECTS + ["adaptive"], "default": "adaptive"},
                "duration": {"type": "int", "default": 5, "min": 3, "max": 12},
                "resolution": {"type": "choice", "choices": ["480p", "720p"], "default": "720p"},
            },
        ),
        ModelSpec(
            id="doubao-seedream-4-0-250828",
            label="Seedream 4.0 - image generation",
            capabilities=[Capability.TEXT_TO_IMAGE, Capability.IMAGE_EDIT],
            options={
                "size": {"type": "choice", "choices": ["1K", "2K", "4K"], "default": "2K"},
                "candidate_count": {"type": "int", "default": 1, "min": 1, "max": 4},
            },
        ),
    ]

    @property
    def base_url(self) -> str:
        region = getattr(self.settings, "seedance_region", "international")
        return self.REGIONS.get(region, self.REGIONS["international"])

    def _headers(self) -> dict[str, str]:
        self._require_key()
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    @property
    def _proxy(self) -> str:
        return getattr(self.settings, "proxy_url", "") or ""

    def generate(self, request, progress=lambda f, m: None, should_cancel=None) -> GenerationResult:
        started = time.time()
        if request.capability in (Capability.TEXT_TO_IMAGE, Capability.IMAGE_EDIT):
            result = self._image(request, progress)
        else:
            result = self._video(request, progress, should_cancel)
        result.provider, result.model_id = self.key, request.model_id
        result.elapsed_seconds = time.time() - started
        return result

    def _video(self, request: GenerationRequest, progress, should_cancel) -> GenerationResult:
        progress(0.05, "Submitting Seedance task")
        # Ark takes generation switches as inline text commands on the prompt.
        commands = []
        options = request.options
        if options.get("ratio"):
            commands.append(f"--ratio {options['ratio']}")
        if options.get("duration"):
            commands.append(f"--dur {int(options['duration'])}")
        if options.get("resolution"):
            commands.append(f"--rs {options['resolution']}")
        if options.get("camerafixed"):
            commands.append("--camerafixed true")
        commands.append(f"--wm {'true' if options.get('watermark') else 'false'}")
        if request.seed is not None:
            commands.append(f"--seed {request.seed}")

        content: list[dict[str, Any]] = [
            {"type": "text", "text": f"{request.prompt} {' '.join(commands)}".strip()}
        ]
        for index, image in enumerate(request.reference_images[:2]):
            entry: dict[str, Any] = {"type": "image_url", "image_url": {"url": data_uri(image)}}
            # Two images means first-frame / last-frame keyframe control.
            if len(request.reference_images) > 1:
                entry["role"] = "first_frame" if index == 0 else "last_frame"
            content.append(entry)

        created = request_json(
            "POST", f"{self.base_url}/contents/generations/tasks",
            headers=self._headers(),
            json_body={"model": request.model_id, "content": content},
            proxy=self._proxy,
        )
        task_id = created.get("id")
        if not task_id:
            raise ProviderError(f"Seedance did not return a task id: {created}")

        final = poll_until(
            fetch=lambda: request_json(
                "GET", f"{self.base_url}/contents/generations/tasks/{task_id}",
                headers=self._headers(), proxy=self._proxy,
            ),
            is_done=lambda payload: payload.get("status") == "succeeded",
            is_failed=lambda payload: (
                f"Seedance task failed: {payload.get('error') or payload.get('status')}"
                if payload.get("status") in ("failed", "cancelled") else None
            ),
            progress=progress, label="Seedance rendering", interval=6.0, max_wait=1800.0,
            should_cancel=should_cancel,
        )

        url = final.get("content", {}).get("video_url", "")
        if not url:
            raise ProviderError(f"Seedance succeeded but returned no video url: {final}")
        progress(0.95, "Downloading video")
        destination = self._stamped_path(request.output_dir, "seedance", ".mp4")
        download(url, destination, proxy=self._proxy)
        usage = final.get("usage", {})
        return GenerationResult(
            files=[destination], raw=final,
            cost_note=f"{usage.get('total_tokens', '?')} tokens",
        )

    def _image(self, request: GenerationRequest, progress) -> GenerationResult:
        progress(0.15, "Requesting Seedream image")
        body: dict[str, Any] = {
            "model": request.model_id,
            "prompt": request.prompt,
            "size": request.options.get("size", "2K"),
            "n": int(request.options.get("candidate_count", 1)),
            "response_format": "url",
            "watermark": bool(request.options.get("watermark", False)),
        }
        if request.reference_images:
            body["image"] = [data_uri(path) for path in request.reference_images[:4]]

        payload = request_json(
            "POST", f"{self.base_url}/images/generations",
            headers=self._headers(), json_body=body, proxy=self._proxy, timeout=300,
        )
        files: list[Path] = []
        for entry in payload.get("data", []):
            destination = self._stamped_path(request.output_dir, "seedream", ".png")
            if entry.get("url"):
                download(entry["url"], destination, proxy=self._proxy)
            elif entry.get("b64_json"):
                destination.write_bytes(base64.b64decode(entry["b64_json"]))
            else:
                continue
            files.append(destination)
        if not files:
            raise ProviderError(f"Seedream returned no images: {str(payload)[:400]}")
        progress(1.0, "Done")
        return GenerationResult(files=files, raw=payload)


# ======================================================================================
# Alibaba DashScope: Qwen-Omni (any-to-any) and Wan video
# ======================================================================================

class OmniProvider(Provider):
    key = "omni"
    name = "Qwen-Omni / Wan (Alibaba DashScope)"
    homepage = "https://dashscope.console.aliyun.com/"
    credential_slot = "dashscope"
    credential_help = "Create a DashScope key at dashscope.console.aliyun.com (or the intl console)."

    COMPAT_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    ASYNC_ROOT = "https://dashscope-intl.aliyuncs.com/api/v1"

    models = [
        ModelSpec(
            id="qwen3-omni-flash",
            label="Qwen3-Omni Flash - text/image/audio/video in, text+speech out",
            capabilities=[Capability.LLM, Capability.VIDEO_UNDERSTANDING, Capability.TEXT_TO_SPEECH],
            notes="Omni-modal. Feed it a clip and ask for a shotlist, captions, or a voiceover script.",
            options={
                "temperature": {"type": "float", "default": 0.8, "min": 0.0, "max": 2.0},
                "voice": {"type": "choice", "choices": ["Cherry", "Ethan", "Nofish", "Jada"],
                          "default": "Cherry"},
                "speak": {"type": "bool", "default": False, "label": "Also synthesise speech"},
            },
        ),
        ModelSpec(
            id="qwen3-omni-30b-a3b-instruct",
            label="Qwen3-Omni 30B - open-weights omni model",
            capabilities=[Capability.LLM, Capability.VIDEO_UNDERSTANDING],
            options={"temperature": {"type": "float", "default": 0.8, "min": 0.0, "max": 2.0}},
        ),
        ModelSpec(
            id="wan2.5-t2v-preview",
            label="Wan 2.5 - text to video",
            capabilities=[Capability.TEXT_TO_VIDEO],
            options={
                "size": {"type": "choice", "choices": ["1280*720", "720*1280", "960*960"],
                         "default": "1280*720"},
                "duration": {"type": "int", "default": 5, "min": 3, "max": 10},
            },
        ),
        ModelSpec(
            id="wan2.5-i2v-preview",
            label="Wan 2.5 - image to video",
            capabilities=[Capability.IMAGE_TO_VIDEO],
            options={"duration": {"type": "int", "default": 5, "min": 3, "max": 10}},
        ),
    ]

    def _headers(self, asynchronous: bool = False) -> dict[str, str]:
        self._require_key()
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        if asynchronous:
            headers["X-DashScope-Async"] = "enable"
        return headers

    @property
    def _proxy(self) -> str:
        return getattr(self.settings, "proxy_url", "") or ""

    def generate(self, request, progress=lambda f, m: None, should_cancel=None) -> GenerationResult:
        started = time.time()
        if request.model_id.startswith("wan"):
            result = self._video(request, progress, should_cancel)
        else:
            result = self._omni_chat(request, progress)
        result.provider, result.model_id = self.key, request.model_id
        result.elapsed_seconds = time.time() - started
        return result

    def _omni_chat(self, request: GenerationRequest, progress) -> GenerationResult:
        progress(0.2, "Calling Qwen-Omni")
        content: list[dict[str, Any]] = []
        for image in request.reference_images[:8]:
            content.append({"type": "image_url", "image_url": {"url": data_uri(image)}})
        if request.reference_video:
            content.append({"type": "video_url",
                            "video_url": {"url": data_uri(request.reference_video)}})
        if request.audio_input:
            content.append({"type": "input_audio",
                            "input_audio": {"data": data_uri(request.audio_input), "format": "wav"}})
        content.append({"type": "text", "text": request.prompt})

        body: dict[str, Any] = {
            "model": request.model_id,
            "messages": [{"role": "user", "content": content}],
            "temperature": float(request.options.get("temperature", 0.8)),
        }
        if request.options.get("speak"):
            body["modalities"] = ["text", "audio"]
            body["audio"] = {"voice": request.options.get("voice", "Cherry"), "format": "wav"}
            body["stream"] = False

        payload = request_json(
            "POST", f"{self.COMPAT_URL}/chat/completions",
            headers=self._headers(), json_body=body, proxy=self._proxy, timeout=600,
        )
        choice = (payload.get("choices") or [{}])[0]
        message = choice.get("message", {})
        text = message.get("content") or ""
        if isinstance(text, list):
            text = "".join(part.get("text", "") for part in text if isinstance(part, dict))

        files: list[Path] = []
        audio = message.get("audio") or {}
        if audio.get("data"):
            destination = self._stamped_path(request.output_dir, "omni-speech", ".wav")
            destination.write_bytes(base64.b64decode(audio["data"]))
            files.append(destination)
            text = text or audio.get("transcript", "")

        progress(1.0, "Done")
        return GenerationResult(files=files, text=text, raw=payload)

    def _video(self, request: GenerationRequest, progress, should_cancel) -> GenerationResult:
        progress(0.05, "Submitting Wan task")
        inputs: dict[str, Any] = {"prompt": request.prompt}
        if request.negative_prompt:
            inputs["negative_prompt"] = request.negative_prompt
        if request.reference_images:
            inputs["img_url"] = data_uri(request.reference_images[0])

        parameters = {"duration": int(request.options.get("duration", 5))}
        if request.options.get("size"):
            parameters["size"] = request.options["size"]

        created = request_json(
            "POST", f"{self.ASYNC_ROOT}/services/aigc/video-generation/video-synthesis",
            headers=self._headers(asynchronous=True),
            json_body={"model": request.model_id, "input": inputs, "parameters": parameters},
            proxy=self._proxy,
        )
        task_id = created.get("output", {}).get("task_id")
        if not task_id:
            raise ProviderError(f"DashScope did not return a task id: {created}")

        final = poll_until(
            fetch=lambda: request_json(
                "GET", f"{self.ASYNC_ROOT}/tasks/{task_id}", headers=self._headers(),
                proxy=self._proxy,
            ),
            is_done=lambda p: p.get("output", {}).get("task_status") == "SUCCEEDED",
            is_failed=lambda p: (
                f"Wan task failed: {p.get('output', {}).get('message', p)}"
                if p.get("output", {}).get("task_status") in ("FAILED", "CANCELED", "UNKNOWN")
                else None
            ),
            progress=progress, label="Wan rendering", interval=6.0, max_wait=1800.0,
            should_cancel=should_cancel,
        )
        url = final.get("output", {}).get("video_url", "")
        if not url:
            raise ProviderError(f"Wan returned no video url: {final}")
        destination = self._stamped_path(request.output_dir, "wan", ".mp4")
        progress(0.95, "Downloading video")
        download(url, destination, proxy=self._proxy)
        return GenerationResult(files=[destination], raw=final)


# ======================================================================================
# fal.ai -- generic gateway to the open-source model long tail
# ======================================================================================

DEFAULT_FAL_MODELS = [
    ("fal-ai/flux/dev", "FLUX.1 dev - image", [Capability.TEXT_TO_IMAGE]),
    ("fal-ai/flux-pro/kontext", "FLUX Kontext - image edit", [Capability.IMAGE_EDIT]),
    ("fal-ai/nano-banana", "Nano Banana via fal", [Capability.TEXT_TO_IMAGE, Capability.IMAGE_EDIT]),
    ("fal-ai/bytedance/seedance/v1/pro/text-to-video", "Seedance Pro via fal",
     [Capability.TEXT_TO_VIDEO]),
    ("fal-ai/bytedance/seedance/v1/pro/image-to-video", "Seedance Pro i2v via fal",
     [Capability.IMAGE_TO_VIDEO]),
    ("fal-ai/veo3", "Veo 3 via fal", [Capability.TEXT_TO_VIDEO]),
    ("fal-ai/kling-video/v2/master/text-to-video", "Kling 2 Master", [Capability.TEXT_TO_VIDEO]),
    ("fal-ai/minimax/hailuo-02/standard/text-to-video", "Hailuo 02", [Capability.TEXT_TO_VIDEO]),
    ("fal-ai/wan-t2v", "Wan 2.1 (open weights)", [Capability.TEXT_TO_VIDEO]),
    ("fal-ai/ltx-video-13b-distilled", "LTX-Video 13B", [Capability.TEXT_TO_VIDEO]),
    ("fal-ai/hunyuan-video", "HunyuanVideo", [Capability.TEXT_TO_VIDEO]),
    ("fal-ai/topaz/upscale/video", "Topaz video upscale", [Capability.UPSCALE]),
    ("fal-ai/esrgan", "Real-ESRGAN image upscale", [Capability.UPSCALE]),
    ("fal-ai/whisper", "Whisper transcription", [Capability.TRANSCRIBE]),
]


class FalProvider(Provider):
    key = "fal"
    name = "fal.ai (open-source model gateway)"
    homepage = "https://fal.ai/models"
    credential_slot = "fal"
    credential_help = "Create a key at fal.ai/dashboard/keys. Format: key_id:key_secret."

    QUEUE_ROOT = "https://queue.fal.run"

    def __init__(self, api_key: str = "", settings: Any = None) -> None:
        super().__init__(api_key, settings)
        self.models = [
            ModelSpec(
                id=model_id, label=label, capabilities=list(capabilities),
                options={
                    "aspect_ratio": {"type": "choice", "choices": ASPECTS, "default": "16:9"},
                    "duration": {"type": "int", "default": 5, "min": 2, "max": 10},
                    "num_images": {"type": "int", "default": 1, "min": 1, "max": 4},
                    "extra_json": {"type": "text", "default": "",
                                   "label": "Extra JSON payload (merged verbatim)"},
                },
            )
            for model_id, label, capabilities in DEFAULT_FAL_MODELS
        ] + self._custom_models()

    def _custom_models(self) -> list[ModelSpec]:
        """Any model id the user pastes in Settings shows up here."""
        raw = getattr(self.settings, "custom_fal_models", "") or ""
        specs: list[ModelSpec] = []
        for line in raw.splitlines():
            model_id = line.strip()
            if not model_id or model_id.startswith("#"):
                continue
            specs.append(ModelSpec(
                id=model_id, label=f"{model_id} (custom)",
                capabilities=list(Capability),
                notes="User-added fal model. Use the Extra JSON field for model-specific inputs.",
                options={"extra_json": {"type": "text", "default": "",
                                        "label": "Extra JSON payload"}},
            ))
        return specs

    def _headers(self) -> dict[str, str]:
        self._require_key()
        return {"Authorization": f"Key {self.api_key}", "Content-Type": "application/json"}

    @property
    def _proxy(self) -> str:
        return getattr(self.settings, "proxy_url", "") or ""

    def generate(self, request, progress=lambda f, m: None, should_cancel=None) -> GenerationResult:
        started = time.time()
        progress(0.05, "Queueing fal job")

        payload: dict[str, Any] = {"prompt": request.prompt}
        if request.negative_prompt:
            payload["negative_prompt"] = request.negative_prompt
        if request.seed is not None:
            payload["seed"] = request.seed
        options = request.options
        if options.get("aspect_ratio"):
            payload["aspect_ratio"] = options["aspect_ratio"]
        if request.capability in (Capability.TEXT_TO_VIDEO, Capability.IMAGE_TO_VIDEO):
            payload["duration"] = int(options.get("duration", 5))
        if request.capability == Capability.TEXT_TO_IMAGE:
            payload["num_images"] = int(options.get("num_images", 1))
        if request.reference_images:
            payload["image_url"] = data_uri(request.reference_images[0])
            payload["image_urls"] = [data_uri(path) for path in request.reference_images[:4]]
        if request.reference_video:
            payload["video_url"] = data_uri(request.reference_video)
        if request.audio_input:
            payload["audio_url"] = data_uri(request.audio_input)

        extra = (options.get("extra_json") or "").strip()
        if extra:
            try:
                payload.update(json.loads(extra))
            except json.JSONDecodeError as error:
                raise ProviderError(f"Extra JSON is not valid JSON: {error}") from error

        submitted = request_json(
            "POST", f"{self.QUEUE_ROOT}/{request.model_id}",
            headers=self._headers(), json_body=payload, proxy=self._proxy, timeout=300,
        )
        status_url = submitted.get("status_url")
        response_url = submitted.get("response_url")
        if not status_url:
            raise ProviderError(f"fal did not return a queue handle: {str(submitted)[:400]}")

        poll_until(
            fetch=lambda: request_json("GET", status_url, headers=self._headers(),
                                       proxy=self._proxy),
            is_done=lambda p: p.get("status") == "COMPLETED",
            is_failed=lambda p: (
                f"fal job {p.get('status')}: {str(p)[:400]}"
                if p.get("status") in ("FAILED", "CANCELLED", "ERROR") else None
            ),
            progress=progress, label="fal running", interval=4.0, max_wait=1800.0,
            should_cancel=should_cancel,
        )
        final = request_json("GET", response_url, headers=self._headers(), proxy=self._proxy)

        files = self._collect_outputs(final, request.output_dir)
        text = final.get("text") or final.get("output") if isinstance(final.get("output"), str) else ""
        if not files and not text:
            raise ProviderError(f"fal returned nothing recognisable:\n{str(final)[:600]}")

        return GenerationResult(
            files=files, text=text or "", raw=final, provider=self.key,
            model_id=request.model_id, elapsed_seconds=time.time() - started,
        )

    def _collect_outputs(self, payload: dict, output_dir: Path) -> list[Path]:
        """fal's output shape varies per model; walk it and grab every media url."""
        urls: list[str] = []

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                url = node.get("url")
                if isinstance(url, str) and url.startswith("http"):
                    urls.append(url)
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(payload)
        files: list[Path] = []
        for url in dict.fromkeys(urls):
            suffix = _suffix_from_url(url, ".bin")
            if suffix.lower() not in {".mp4", ".webm", ".mov", ".png", ".jpg", ".jpeg",
                                      ".webp", ".gif", ".wav", ".mp3", ".m4a"}:
                continue
            destination = self._stamped_path(output_dir, "fal", suffix)
            download(url, destination, proxy=self._proxy)
            files.append(destination)
        return files


# ======================================================================================
# Replicate -- the other big open-source gateway
# ======================================================================================

DEFAULT_REPLICATE_MODELS = [
    ("black-forest-labs/flux-dev", "FLUX.1 dev", [Capability.TEXT_TO_IMAGE]),
    ("google/nano-banana", "Nano Banana via Replicate",
     [Capability.TEXT_TO_IMAGE, Capability.IMAGE_EDIT]),
    ("bytedance/seedance-1-pro", "Seedance 1 Pro via Replicate",
     [Capability.TEXT_TO_VIDEO, Capability.IMAGE_TO_VIDEO]),
    ("wan-video/wan-2.2-t2v-fast", "Wan 2.2 fast", [Capability.TEXT_TO_VIDEO]),
    ("lightricks/ltx-video", "LTX-Video", [Capability.TEXT_TO_VIDEO]),
    ("nightmareai/real-esrgan", "Real-ESRGAN upscale", [Capability.UPSCALE]),
    ("openai/whisper", "Whisper transcription", [Capability.TRANSCRIBE]),
]


class ReplicateProvider(Provider):
    key = "replicate"
    name = "Replicate (open-source model gateway)"
    homepage = "https://replicate.com/explore"
    credential_slot = "replicate"
    credential_help = "Create a token at replicate.com/account/api-tokens."

    ROOT = "https://api.replicate.com/v1"

    def __init__(self, api_key: str = "", settings: Any = None) -> None:
        super().__init__(api_key, settings)
        self.models = [
            ModelSpec(
                id=model_id, label=label, capabilities=list(capabilities),
                options={"extra_json": {"type": "text", "default": "",
                                        "label": "Extra JSON input (merged verbatim)"}},
            )
            for model_id, label, capabilities in DEFAULT_REPLICATE_MODELS
        ]
        raw = getattr(self.settings, "custom_replicate_models", "") or ""
        for line in raw.splitlines():
            model_id = line.strip()
            if model_id and not model_id.startswith("#"):
                self.models.append(ModelSpec(
                    id=model_id, label=f"{model_id} (custom)", capabilities=list(Capability),
                    options={"extra_json": {"type": "text", "default": ""}},
                ))

    def _headers(self) -> dict[str, str]:
        self._require_key()
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    @property
    def _proxy(self) -> str:
        return getattr(self.settings, "proxy_url", "") or ""

    def generate(self, request, progress=lambda f, m: None, should_cancel=None) -> GenerationResult:
        started = time.time()
        progress(0.05, "Creating Replicate prediction")

        inputs: dict[str, Any] = {"prompt": request.prompt}
        if request.reference_images:
            inputs["image"] = data_uri(request.reference_images[0])
        if request.reference_video:
            inputs["video"] = data_uri(request.reference_video)
        if request.audio_input:
            inputs["audio"] = data_uri(request.audio_input)
        extra = (request.options.get("extra_json") or "").strip()
        if extra:
            try:
                inputs.update(json.loads(extra))
            except json.JSONDecodeError as error:
                raise ProviderError(f"Extra JSON is not valid JSON: {error}") from error

        owner, _, name = request.model_id.partition("/")
        created = request_json(
            "POST", f"{self.ROOT}/models/{owner}/{name}/predictions",
            headers=self._headers(), json_body={"input": inputs}, proxy=self._proxy,
        )
        get_url = created.get("urls", {}).get("get")
        if not get_url:
            raise ProviderError(f"Replicate did not return a poll url: {str(created)[:400]}")

        final = poll_until(
            fetch=lambda: request_json("GET", get_url, headers=self._headers(), proxy=self._proxy),
            is_done=lambda p: p.get("status") == "succeeded",
            is_failed=lambda p: (
                f"Replicate prediction {p.get('status')}: {p.get('error')}"
                if p.get("status") in ("failed", "canceled") else None
            ),
            progress=progress, label="Replicate running", interval=4.0, max_wait=1800.0,
            should_cancel=should_cancel,
        )

        output = final.get("output")
        urls = [output] if isinstance(output, str) else [
            item for item in (output or []) if isinstance(item, str)
        ]
        files: list[Path] = []
        for url in urls:
            if not url.startswith("http"):
                continue
            destination = self._stamped_path(request.output_dir, "replicate",
                                             _suffix_from_url(url, ".png"))
            download(url, destination, proxy=self._proxy)
            files.append(destination)

        text = output if isinstance(output, str) and not output.startswith("http") else ""
        return GenerationResult(
            files=files, text=text, raw=final, provider=self.key,
            model_id=request.model_id, elapsed_seconds=time.time() - started,
        )


# ======================================================================================
# ComfyUI -- local, free, no API key
# ======================================================================================

class ComfyUIProvider(Provider):
    key = "comfyui"
    name = "ComfyUI (local workflows)"
    homepage = "https://github.com/comfyanonymous/ComfyUI"
    credential_slot = ""
    credential_help = "No key needed. Point Settings -> ComfyUI URL at your running instance."

    models = [
        ModelSpec(
            id=comfy_builtin.WAN22_ID,
            label="Wan 2.2 5B (built in)",
            capabilities=[Capability.IMAGE_TO_VIDEO, Capability.TEXT_TO_VIDEO],
            notes="Runs on this PC through ComfyUI with the Wan 2.2 TI2V 5B weights -- no "
                  "workflow to export. A photo comes to life: foliage, water and light move; "
                  "the camera stays put, and the photo film adds its own camera move. "
                  "Standard quality takes about 4-5 minutes per clip on an 8 GB laptop GPU. "
                  "There is no faster setting: below Standard the model starts inventing "
                  "people and objects that are not in the photo.",
            options={
                "quality": {"type": "choice", "default": "Standard",
                            "choices": list(comfy_builtin.SIZES), "label": "Quality"},
                "seconds": {"type": "float", "default": 3.4, "min": 0.8, "max": 3.4,
                            "label": "Length (seconds)"},
                "cfg": {"type": "float", "default": 5.0, "min": 1.0, "max": 10.0,
                        "label": "Prompt strength"},
            },
        ),
        *comfy_local.SPECS,
        ModelSpec(
            id="workflow",
            label="Run a saved API-format workflow",
            capabilities=[Capability.TEXT_TO_IMAGE, Capability.IMAGE_EDIT,
                          Capability.TEXT_TO_VIDEO, Capability.IMAGE_TO_VIDEO, Capability.UPSCALE],
            notes="Export any ComfyUI graph with 'Save (API format)'. Nodes whose title contains "
                  "PROMPT get the prompt text injected automatically.",
            options={
                "workflow_path": {"type": "file", "default": "",
                                  "label": "Workflow JSON (API format)"},
                "prompt_node_title": {"type": "str", "default": "PROMPT",
                                      "label": "Inject prompt into nodes titled"},
            },
        )
    ]

    # Reachability is cached: the UI asks constantly, and a dead port costs a full
    # connect timeout every time otherwise.
    _reach_cache: tuple[float, bool] | None = None
    REACH_TTL = 20.0

    @property
    def base_url(self) -> str:
        return (getattr(self.settings, "comfyui_url", "") or "http://127.0.0.1:8188").rstrip("/")

    @property
    def _proxy(self) -> str:
        return ""  # local host: never proxy

    def reachable(self, force: bool = False) -> bool:
        """Is a ComfyUI server actually listening?

        'No API key needed' is not the same as 'usable'. Claiming a model is free and
        ready when nothing is running just moves the failure later, into a render.
        """
        now = time.time()
        cached = type(self)._reach_cache
        if not force and cached and now - cached[0] < self.REACH_TTL:
            return cached[1]

        alive = False
        try:
            import socket
            from urllib.parse import urlparse

            parsed = urlparse(self.base_url)
            host = parsed.hostname or "127.0.0.1"
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            with socket.create_connection((host, port), timeout=0.6):
                alive = True
        except OSError:
            alive = False

        type(self)._reach_cache = (now, alive)
        return alive

    def builtin_status(self, model_id: str) -> tuple[bool, str]:
        """Whether a built-in graph can run now, and if not, the exact reason."""
        if model_id != comfy_builtin.WAN22_ID and model_id not in comfy_local.SPEC_IDS:
            return False, "Not a built-in model."
        label = self.model(model_id).label.replace(" (built in)", "").split(" (")[0]
        if not self.reachable():
            return False, (f"ComfyUI is not running at {self.base_url}. Start it and {label} "
                           "runs on this PC.")
        if model_id == comfy_builtin.WAN22_ID:
            if comfy_builtin.find_wan22_files(self.base_url) is None:
                return False, ("ComfyUI is running but the Wan 2.2 5B files are missing: "
                               "wan2.2_ti2v_5B_fp16 (diffusion_models), umt5_xxl "
                               "(text_encoders) and wan2.2_vae (vae).")
        elif comfy_local.find_files(self.base_url, model_id) is None:
            return False, (f"ComfyUI is running but {label} needs "
                           f"{comfy_local.MISSING[model_id]}.")
        return True, "Runs on this PC through ComfyUI."

    def generate(self, request, progress=lambda f, m: None, should_cancel=None) -> GenerationResult:
        if request.model_id == comfy_builtin.WAN22_ID:
            return self._generate_wan22(request, progress, should_cancel)
        if request.model_id in comfy_local.SPEC_IDS:
            return self._generate_local(request, progress, should_cancel)
        started = time.time()
        workflow_path = Path(request.options.get("workflow_path", ""))
        if not workflow_path.is_file():
            raise ProviderError(
                "Select a ComfyUI workflow exported in API format "
                "(ComfyUI menu -> Workflow -> Export (API))."
            )
        try:
            graph = json.loads(workflow_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ProviderError(f"Workflow JSON is invalid: {error}") from error

        marker = (request.options.get("prompt_node_title") or "PROMPT").upper()
        injected = 0
        for node in graph.values():
            if not isinstance(node, dict):
                continue
            title = str(node.get("_meta", {}).get("title", "")).upper()
            inputs = node.get("inputs", {})
            if marker in title and "text" in inputs:
                inputs["text"] = request.prompt
                injected += 1
            elif node.get("class_type") == "KSampler" and request.seed is not None:
                inputs["seed"] = request.seed
        if injected == 0:
            progress(0.05, f"No node titled '{marker}' -- running workflow unchanged")

        client_id = str(uuid.uuid4())
        progress(0.1, "Queueing ComfyUI prompt")
        queued = request_json(
            "POST", f"{self.base_url}/prompt",
            json_body={"prompt": graph, "client_id": client_id},
        )
        prompt_id = queued.get("prompt_id")
        if not prompt_id:
            raise ProviderError(f"ComfyUI rejected the workflow: {str(queued)[:500]}")

        history = poll_until(
            fetch=lambda: request_json("GET", f"{self.base_url}/history/{prompt_id}"),
            is_done=lambda p: bool(p.get(prompt_id, {}).get("outputs")),
            is_failed=lambda p: (
                "ComfyUI reported an execution error -- check the ComfyUI console."
                if p.get(prompt_id, {}).get("status", {}).get("status_str") == "error" else None
            ),
            progress=progress, label="ComfyUI running", interval=2.0, max_wait=3600.0,
            should_cancel=should_cancel,
        )

        files: list[Path] = []
        outputs = history.get(prompt_id, {}).get("outputs", {})
        for node_output in outputs.values():
            for key in ("images", "gifs", "videos"):
                for item in node_output.get(key, []):
                    url = (
                        f"{self.base_url}/view?filename={item.get('filename', '')}"
                        f"&subfolder={item.get('subfolder', '')}&type={item.get('type', 'output')}"
                    )
                    suffix = Path(item.get("filename", "out.png")).suffix or ".png"
                    destination = self._stamped_path(request.output_dir, "comfy", suffix)
                    download(url, destination)
                    files.append(destination)

        if not files:
            raise ProviderError("ComfyUI finished but produced no saved output nodes.")
        return GenerationResult(
            files=files, raw=history, provider=self.key, model_id=request.model_id,
            elapsed_seconds=time.time() - started, cost_note="runs on this PC",
        )

    # -- built-in Wan 2.2 -----------------------------------------------------------------
    def _generate_wan22(self, request, progress, should_cancel) -> GenerationResult:
        started = time.time()
        ready, reason = self.builtin_status(request.model_id)
        if not ready:
            raise ProviderError(reason)
        files = comfy_builtin.find_wan22_files(self.base_url)
        options = request.options

        quality = comfy_builtin.usable_quality(str(options.get("quality") or "Standard"))
        aspect = str(options.get("aspect_ratio") or "16:9")
        try:
            aspect_width, aspect_height = (int(part) for part in aspect.split(":"))
        except ValueError:
            aspect_width, aspect_height = 16, 9
        width, height = comfy_builtin.size_for(aspect_width, aspect_height, quality)
        seconds = float(options.get("seconds") or options.get("duration_seconds")
                        or options.get("duration") or 3.4)
        length = comfy_builtin.frames_for(seconds)
        steps = int(options.get("steps") or comfy_builtin.STEPS.get(quality, 20))
        cfg = float(options.get("cfg") or 5.0)
        seed = request.seed if request.seed is not None else int(time.time()) % 2_147_483_647
        prompt = request.prompt.strip() or (
            "Natural, subtle motion in the scene; the camera holds steady. Keep everything "
            "in the image exactly as it is.")
        negative = ", ".join(part for part in (request.negative_prompt.strip(),
                                               comfy_builtin.DEFAULT_NEGATIVE) if part)

        image_name = None
        if request.capability == Capability.IMAGE_TO_VIDEO or request.reference_images:
            if not request.reference_images:
                raise ProviderError("Image to video needs a photo to start from.")
            progress(0.02, "Sending the photo to ComfyUI")
            image_name = comfy_builtin.upload_image(self.base_url,
                                                    Path(request.reference_images[0]))

        single_file = comfy_builtin.has_node(self.base_url, "SaveWEBM")
        prefix = f"mediaforge/wan_{uuid.uuid4().hex[:8]}"
        graph = comfy_builtin.wan22_graph(files, prompt, negative, image_name, width, height,
                                          length, steps, cfg, seed, prefix, single_file)

        stage_labels = {"1": (0.04, "Loading Wan 2.2"), "2": (0.06, "Loading the text encoder"),
                        "10": (0.92, "Decoding the frames"), "11": (0.96, "Saving the clip")}

        def on_step(value: int, maximum: int, node: str) -> None:
            if value >= 0 and node == "9":
                progress(0.1 + 0.8 * value / max(1, maximum),
                         f"Wan 2.2 on this PC: step {value} of {maximum}")
            elif value < 0 and node in stage_labels:
                progress(*stage_labels[node])

        self._make_room_on_gpu()
        client_id = str(uuid.uuid4())
        listener = comfy_builtin.ProgressSocket(self.base_url, client_id, on_step)
        listener.start()
        listener.connected.wait(3.0)
        try:
            queued = request_json("POST", f"{self.base_url}/prompt",
                                  json_body={"prompt": graph, "client_id": client_id})
            prompt_id = queued.get("prompt_id")
            if not prompt_id:
                raise ProviderError(f"ComfyUI rejected the Wan 2.2 graph: {str(queued)[:400]}")
            listener.prompt_id = prompt_id
            progress(0.08, "Queued in ComfyUI")
            entry = self._wait_for(prompt_id, should_cancel)
        finally:
            listener.stop()

        progress(0.97, "Fetching the clip")
        output = entry.get("outputs", {}).get("11", {})
        items = output.get("images", []) or output.get("videos", []) or output.get("gifs", [])
        if not items:
            raise ProviderError("ComfyUI finished but saved no clip.")
        destination = self._stamped_path(request.output_dir, "wan22", ".webm" if single_file
                                         else ".mp4")
        if single_file:
            self._fetch(items[0], destination)
        else:
            self._assemble_frames(items, destination)

        elapsed = time.time() - started
        comfy_builtin.record_timing(quality, length, elapsed)
        return GenerationResult(
            files=[destination],
            # The model animates the scene but barely moves the camera; the photo film
            # reads this and adds the shot's own camera move when it conforms the clip.
            raw={"camera": "static", "prompt_id": prompt_id, "size": [width, height],
                 "frames": length, "fps": comfy_builtin.WAN22_FPS, "steps": steps},
            provider=self.key, model_id=request.model_id, elapsed_seconds=elapsed,
            cost_note="runs on this PC",
        )

    # -- the other built-in models: SDXL, ACE-Step, Real-ESRGAN, Wan S2V (comfy_local) ----
    def _generate_local(self, request, progress, should_cancel) -> GenerationResult:
        started = time.time()
        ready, reason = self.builtin_status(request.model_id)
        if not ready:
            raise ProviderError(reason)
        model_id = request.model_id
        files = comfy_local.find_files(self.base_url, model_id)
        options = request.options
        seed = request.seed if request.seed is not None else int(time.time()) % 2_147_483_647
        prefix = f"mediaforge/{model_id}_{uuid.uuid4().hex[:8]}"
        audio_track: Path | None = None
        raw: dict[str, Any] = {"seed": seed}

        if model_id == comfy_local.SDXL_ID:
            aspect = str(options.get("aspect_ratio") or "16:9")
            width, height = comfy_local.SDXL_SIZES.get(aspect, comfy_local.SDXL_SIZES["16:9"])
            steps = comfy_local.SDXL_STEPS.get(str(options.get("quality") or "Standard"), 30)
            image_name = None
            if request.capability == Capability.IMAGE_EDIT or request.reference_images:
                if not request.reference_images:
                    raise ProviderError("An image edit needs the image to change.")
                progress(0.02, "Sending the image to ComfyUI")
                image_name = comfy_builtin.upload_image(self.base_url,
                                                        Path(request.reference_images[0]))
            negative = ", ".join(part for part in (request.negative_prompt.strip(),
                                                   comfy_local.SDXL_NEGATIVE) if part)
            graph = comfy_local.sdxl_graph(
                files, request.prompt.strip(), negative, width, height, steps,
                float(options.get("cfg") or 5.5), seed, prefix, image_name,
                float(options.get("strength") or 0.7))
            sampler, output, suffix, label = "5", "7", ".png", "SDXL"
            raw.update(size=[width, height], steps=steps)
        elif model_id == comfy_local.ACE_STEP_ID:
            seconds = float(options.get("seconds") or 30.0)
            graph = comfy_local.ace_step_graph(
                files, request.prompt.strip() or "calm, warm instrumental background music",
                str(options.get("lyrics") or "").strip(), seconds,
                int(options.get("bpm") or 100), str(options.get("key") or "C major"),
                str(options.get("language") or "en"), seed, prefix)
            sampler, output, suffix, label = "6", "8", ".mp3", "ACE-Step"
            raw.update(seconds=seconds)
        elif model_id == comfy_local.ESRGAN_ID:
            if not request.reference_images:
                raise ProviderError(
                    "For a video, use Enhance -> Upscale, which runs Real-ESRGAN on every "
                    "frame." if request.reference_video else "Choose the image to upscale.")
            progress(0.02, "Sending the image to ComfyUI")
            image_name = comfy_builtin.upload_image(self.base_url,
                                                    Path(request.reference_images[0]))
            scale = 2 if str(options.get("scale") or "4x") == "2x" else 4
            graph = comfy_local.esrgan_graph(files, image_name, scale, prefix)
            sampler, output, suffix, label = "3", "4", ".png", "Real-ESRGAN"
        else:  # Wan 2.2 S2V
            if not request.reference_images:
                raise ProviderError("Lip sync needs a photo of the face that will speak.")
            if not request.audio_input:
                raise ProviderError("Lip sync needs the voice or song for the face to perform.")
            from PIL import Image

            photo = Path(request.reference_images[0])
            with Image.open(photo) as image:
                photo_width, photo_height = image.size
            quality = str(options.get("quality") or "Draft")
            width, height = comfy_local.s2v_size(photo_width, photo_height, quality)
            seconds = float(options.get("seconds") or 3.0)
            length = comfy_local.s2v_frames(seconds)
            progress(0.02, "Sending the photo and the voice to ComfyUI")
            audio_track = self._cut_audio(Path(request.audio_input), length / comfy_local.S2V_FPS)
            image_name = comfy_builtin.upload_image(self.base_url, photo)
            audio_name = comfy_builtin.upload_image(self.base_url, audio_track)
            negative = ", ".join(part for part in (request.negative_prompt.strip(),
                                                   comfy_local.S2V_NEGATIVE) if part)
            graph = comfy_local.s2v_graph(
                files, request.prompt.strip() or (
                    "The person speaks naturally to the camera, lips moving in time with the "
                    "voice, small natural head movement, steady camera."),
                negative, image_name, audio_name, width, height, length,
                comfy_local.S2V_STEPS.get(quality, 12), float(options.get("cfg") or 4.5),
                seed, prefix)
            sampler, output, suffix, label = "12", "17", ".webm", "Wan 2.2 S2V"
            raw.update(size=[width, height], frames=length, fps=comfy_local.S2V_FPS)

        entry = self._run_graph(graph, sampler, label, progress, should_cancel)
        progress(0.97, "Fetching the result")
        items = self._output_items(entry, output)
        if not items:
            raise ProviderError("ComfyUI finished but saved nothing.")
        destination = self._stamped_path(request.output_dir, model_id.split("-")[0], suffix)
        self._fetch(items[0], destination)
        if audio_track is not None:
            destination = self._add_audio(destination, audio_track)
        return GenerationResult(
            files=[destination], raw=raw, provider=self.key, model_id=model_id,
            elapsed_seconds=time.time() - started, cost_note="runs on this PC",
        )

    STAGE_WORDS = (("Loader", "Loading the model"), ("TextEncode", "Reading the prompt"),
                   ("Encode", "Preparing the inputs"), ("Decode", "Decoding the result"),
                   ("Save", "Saving"))

    def _make_room_on_gpu(self) -> None:
        """Asks Ollama to let go of the GPU before a ComfyUI graph needs it.

        Ollama keeps a model in video memory for five minutes after each answer. On an
        8 GB card that is most of the memory, and a video graph queued in that window
        either spills into system RAM (several times slower) or runs out of memory. The
        language model reloads in seconds the next time it is asked something.
        """
        from .ollama import DEFAULT_URL

        url = (getattr(self.settings, "ollama_url", "") or DEFAULT_URL).rstrip("/")
        try:
            loaded = request_json("GET", f"{url}/api/ps", timeout=2).get("models", [])
            for model in loaded:
                request_json("POST", f"{url}/api/generate", timeout=15,
                             json_body={"model": model.get("name", ""), "keep_alive": 0})
        except (ProviderError, OSError):
            pass  # Ollama not installed or not running: nothing is holding the GPU

    def _run_graph(self, graph: dict, sampler_node: str, label: str, progress,
                   should_cancel) -> dict:
        """Queues a graph, reports the sampler's steps, returns the finished history entry."""
        self._make_room_on_gpu()
        def on_step(value: int, maximum: int, node: str) -> None:
            if value >= 0 and node == sampler_node:
                progress(0.1 + 0.85 * value / max(1, maximum),
                         f"{label} on this PC: step {value} of {maximum}")
            elif value < 0 and node in graph:
                kind = graph[node].get("class_type", "")
                words = next((text for needle, text in self.STAGE_WORDS if needle in kind), "")
                if words:
                    progress(0.06, f"{label}: {words.lower()}")

        client_id = str(uuid.uuid4())
        listener = comfy_builtin.ProgressSocket(self.base_url, client_id, on_step)
        listener.start()
        listener.connected.wait(3.0)
        try:
            queued = request_json("POST", f"{self.base_url}/prompt",
                                  json_body={"prompt": graph, "client_id": client_id})
            prompt_id = queued.get("prompt_id")
            if not prompt_id:
                raise ProviderError(f"ComfyUI rejected the {label} graph: {str(queued)[:400]}")
            listener.prompt_id = prompt_id
            progress(0.05, "Queued in ComfyUI")
            return self._wait_for(prompt_id, should_cancel)
        finally:
            listener.stop()

    @staticmethod
    def _output_items(entry: dict, node: str) -> list[dict]:
        output = entry.get("outputs", {}).get(node, {})
        for key in ("images", "audio", "videos", "gifs"):
            if output.get(key):
                return list(output[key])
        return []

    def _cut_audio(self, source: Path, seconds: float) -> Path:
        """The first `seconds` of a track as WAV: S2V encodes all the audio it is given."""
        import subprocess
        import tempfile

        from ..ffmpeg_tools import FfmpegLocator

        locator = FfmpegLocator()
        locator.require()
        target = Path(tempfile.mkdtemp(prefix="mediaforge_s2v_")) / f"{source.stem}_voice.wav"
        result = subprocess.run(
            [locator.ffmpeg, "-y", "-v", "error", "-i", str(source), "-t", f"{seconds:.3f}",
             "-ac", "1", "-ar", "16000", str(target)],
            capture_output=True, text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode != 0:
            raise ProviderError(f"Could not read the audio track: {result.stderr[-300:]}")
        return target

    def _add_audio(self, video: Path, audio: Path) -> Path:
        """Puts the voice back under the silent clip ComfyUI saved."""
        import subprocess

        from ..ffmpeg_tools import FfmpegLocator

        locator = FfmpegLocator()
        locator.require()
        target = video.with_suffix(".mp4")
        result = subprocess.run(
            [locator.ffmpeg, "-y", "-v", "error", "-i", str(video), "-i", str(audio),
             "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-crf", "16",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest", str(target)],
            capture_output=True, text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode != 0:
            raise ProviderError(f"Could not add the voice to the clip: {result.stderr[-300:]}")
        video.unlink(missing_ok=True)
        return target

    def _wait_for(self, prompt_id: str, should_cancel) -> dict:
        deadline = time.time() + 3600
        while time.time() < deadline:
            if should_cancel and should_cancel():
                # Interrupt only stops the prompt that is running; delete also removes it
                # if it is still waiting in the queue.
                for path, body in (("/interrupt", {}), ("/queue", {"delete": [prompt_id]})):
                    try:
                        request_json("POST", f"{self.base_url}{path}", json_body=body)
                    except ProviderError:
                        pass
                raise ProviderError("Cancelled")
            history = request_json("GET", f"{self.base_url}/history/{prompt_id}")
            entry = history.get(prompt_id)
            if entry:
                status = entry.get("status", {})
                if status.get("status_str") == "error":
                    raise ProviderError(self._explain_error(status))
                if entry.get("outputs"):
                    return entry
            time.sleep(1.5)
        raise ProviderError("ComfyUI did not finish within an hour.")

    @staticmethod
    def _explain_error(status: dict) -> str:
        detail = ""
        for kind, data in status.get("messages", []):
            if kind == "execution_error":
                detail = f"{data.get('exception_type', '')}: {data.get('exception_message', '')}"
        if "out of memory" in detail.lower() or "OutOfMemory" in detail:
            return ("The GPU ran out of memory. Choose Standard rather than High, or close "
                    f"other programs that use the GPU.\n\n{detail.strip()[:400]}")
        return f"ComfyUI stopped with an error.\n\n{detail.strip()[:600] or 'See the ComfyUI console.'}"

    def _fetch(self, item: dict, destination: Path) -> None:
        from urllib.parse import urlencode
        query = urlencode({"filename": item.get("filename", ""),
                           "subfolder": item.get("subfolder", ""),
                           "type": item.get("type", "output")})
        download(f"{self.base_url}/view?{query}", destination)

    def _assemble_frames(self, items: list[dict], destination: Path) -> None:
        """Fallback for ComfyUI builds without SaveWEBM: frames in, one clip out."""
        import shutil
        import subprocess
        import tempfile

        from ..ffmpeg_tools import FfmpegLocator

        locator = FfmpegLocator()
        locator.require()
        folder = Path(tempfile.mkdtemp(prefix="mediaforge_wan_"))
        try:
            for index, item in enumerate(items):
                self._fetch(item, folder / f"frame_{index:05d}.png")
            result = subprocess.run(
                [locator.ffmpeg, "-y", "-v", "error", "-framerate",
                 str(comfy_builtin.WAN22_FPS), "-i", str(folder / "frame_%05d.png"),
                 "-c:v", "libx264", "-crf", "14", "-pix_fmt", "yuv420p", str(destination)],
                capture_output=True, text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode != 0:
                raise ProviderError(f"Could not assemble the frames: {result.stderr[-400:]}")
        finally:
            shutil.rmtree(folder, ignore_errors=True)
