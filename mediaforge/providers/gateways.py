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

    def generate(self, request, progress=lambda f, m: None, should_cancel=None) -> GenerationResult:
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
            elapsed_seconds=time.time() - started, cost_note="local, free",
        )
