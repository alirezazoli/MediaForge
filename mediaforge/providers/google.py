"""Google Gemini API provider: Nano Banana (image), Imagen, Veo (video), Gemini (text/video).

All three families sit behind generativelanguage.googleapis.com with a single API key
from aistudio.google.com. Model ids are editable in the UI because Google renames
preview models often -- a rename should be a text edit, not a code change.
"""

from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Any

from .base import (
    Capability, GenerationRequest, GenerationResult, ModelSpec, Provider, ProviderError,
)
from .http import download, poll_until, request_json

API_ROOT = "https://generativelanguage.googleapis.com/v1beta"

ASPECT_CHOICES = ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"]


class GoogleProvider(Provider):
    key = "google"
    name = "Google (Gemini / Imagen / Veo)"
    homepage = "https://aistudio.google.com/apikey"
    credential_slot = "google"
    credential_help = "Create a key at aistudio.google.com/apikey."

    models = [
        ModelSpec(
            id="gemini-2.5-flash-image",
            label="Nano Banana - Gemini 2.5 Flash Image",
            capabilities=[Capability.TEXT_TO_IMAGE, Capability.IMAGE_EDIT],
            notes="Conversational image editing. Accepts up to 3 reference images for "
                  "character/product consistency.",
            options={
                "aspect_ratio": {"type": "choice", "choices": ASPECT_CHOICES, "default": "16:9"},
                "candidate_count": {"type": "int", "default": 1, "min": 1, "max": 4,
                                    "label": "Images"},
            },
        ),
        ModelSpec(
            id="imagen-4.0-generate-001",
            label="Imagen 4 - photoreal stills",
            capabilities=[Capability.TEXT_TO_IMAGE],
            notes="Higher fidelity text-to-image; no reference-image editing.",
            options={
                "aspect_ratio": {"type": "choice", "choices": ASPECT_CHOICES, "default": "16:9"},
                "candidate_count": {"type": "int", "default": 1, "min": 1, "max": 4,
                                    "label": "Images"},
            },
        ),
        ModelSpec(
            id="veo-3.1-generate-preview",
            label="Veo 3.1 - video with native audio",
            capabilities=[Capability.TEXT_TO_VIDEO, Capability.IMAGE_TO_VIDEO],
            notes="Text-to-video and image-to-video with synchronised dialogue and SFX.",
            options={
                "aspect_ratio": {"type": "choice", "choices": ["16:9", "9:16"], "default": "16:9"},
                "duration_seconds": {"type": "int", "default": 8, "min": 4, "max": 8},
                "resolution": {"type": "choice", "choices": ["720p", "1080p"], "default": "720p"},
                "person_generation": {
                    "type": "choice",
                    "choices": ["allow_all", "allow_adult", "dont_allow"],
                    "default": "allow_adult",
                },
            },
        ),
        ModelSpec(
            id="veo-3.1-fast-generate-preview",
            label="Veo 3.1 Fast - cheaper drafts",
            capabilities=[Capability.TEXT_TO_VIDEO, Capability.IMAGE_TO_VIDEO],
            notes="Use for iteration passes, then re-run the winner on full Veo.",
            options={
                "aspect_ratio": {"type": "choice", "choices": ["16:9", "9:16"], "default": "16:9"},
                "duration_seconds": {"type": "int", "default": 8, "min": 4, "max": 8},
            },
        ),
        ModelSpec(
            id="gemini-2.5-pro",
            label="Gemini 2.5 Pro - scripts, shotlists, captions",
            capabilities=[Capability.LLM, Capability.VIDEO_UNDERSTANDING],
            notes="Also reads uploaded video for scene descriptions and timestamps.",
            options={"temperature": {"type": "float", "default": 0.8, "min": 0.0, "max": 2.0}},
        ),
        ModelSpec(
            id="gemini-2.5-flash",
            label="Gemini 2.5 Flash - fast text",
            capabilities=[Capability.LLM, Capability.VIDEO_UNDERSTANDING],
            options={"temperature": {"type": "float", "default": 0.8, "min": 0.0, "max": 2.0}},
        ),
    ]

    # -- plumbing -----------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        self._require_key()
        return {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}

    @property
    def _proxy(self) -> str:
        return getattr(self.settings, "proxy_url", "") or ""

    def generate(self, request, progress=lambda f, m: None, should_cancel=None) -> GenerationResult:
        started = time.time()
        model_id = request.model_id

        if model_id.startswith("veo"):
            result = self._generate_video(request, progress, should_cancel)
        elif model_id.startswith("imagen"):
            result = self._generate_imagen(request, progress)
        elif request.capability in (Capability.TEXT_TO_IMAGE, Capability.IMAGE_EDIT):
            result = self._generate_gemini_image(request, progress)
        else:
            result = self._generate_text(request, progress)

        result.provider = self.key
        result.model_id = model_id
        result.elapsed_seconds = time.time() - started
        return result

    # -- Nano Banana ---------------------------------------------------------------
    def _generate_gemini_image(self, request: GenerationRequest, progress) -> GenerationResult:
        progress(0.1, "Sending prompt to Nano Banana")
        parts: list[dict[str, Any]] = [{"text": request.prompt}]
        for image_path in request.reference_images[:3]:
            parts.append({
                "inline_data": {
                    "mime_type": _mime_for(image_path),
                    "data": base64.b64encode(image_path.read_bytes()).decode("ascii"),
                }
            })

        body: dict[str, Any] = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"responseModalities": ["IMAGE"]},
        }
        aspect = request.options.get("aspect_ratio")
        if aspect:
            body["generationConfig"]["imageConfig"] = {"aspectRatio": aspect}

        count = int(request.options.get("candidate_count", 1))
        files: list[Path] = []
        for index in range(count):
            progress(0.2 + 0.7 * index / count, f"Image {index + 1} of {count}")
            payload = request_json(
                "POST", f"{API_ROOT}/models/{request.model_id}:generateContent",
                headers=self._headers(), json_body=body, proxy=self._proxy,
            )
            files.extend(self._save_inline_images(payload, request.output_dir, "nanobanana"))

        if not files:
            raise ProviderError(
                "Google returned no image. This usually means the prompt was blocked by "
                "safety filters -- try rephrasing, or check the safety settings on your key."
            )
        progress(1.0, "Done")
        return GenerationResult(files=files, cost_note=f"{len(files)} image(s)")

    def _save_inline_images(self, payload: dict, output_dir: Path, prefix: str) -> list[Path]:
        saved: list[Path] = []
        for candidate in payload.get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                blob = part.get("inlineData") or part.get("inline_data")
                if not blob or not blob.get("data"):
                    continue
                mime = blob.get("mimeType") or blob.get("mime_type") or "image/png"
                suffix = ".jpg" if "jpeg" in mime else ".png"
                destination = self._stamped_path(output_dir, prefix, suffix)
                destination.write_bytes(base64.b64decode(blob["data"]))
                saved.append(destination)
        return saved

    # -- Imagen --------------------------------------------------------------------
    def _generate_imagen(self, request: GenerationRequest, progress) -> GenerationResult:
        progress(0.15, "Requesting Imagen render")
        body = {
            "instances": [{"prompt": request.prompt}],
            "parameters": {
                "sampleCount": int(request.options.get("candidate_count", 1)),
                "aspectRatio": request.options.get("aspect_ratio", "16:9"),
            },
        }
        if request.negative_prompt:
            body["parameters"]["negativePrompt"] = request.negative_prompt

        payload = request_json(
            "POST", f"{API_ROOT}/models/{request.model_id}:predict",
            headers=self._headers(), json_body=body, proxy=self._proxy,
        )
        files: list[Path] = []
        for prediction in payload.get("predictions", []):
            data = prediction.get("bytesBase64Encoded")
            if not data:
                continue
            destination = self._stamped_path(request.output_dir, "imagen", ".png")
            destination.write_bytes(base64.b64decode(data))
            files.append(destination)

        if not files:
            raise ProviderError("Imagen returned no images (prompt likely filtered).")
        progress(1.0, "Done")
        return GenerationResult(files=files, cost_note=f"{len(files)} image(s)")

    # -- Veo -----------------------------------------------------------------------
    def _generate_video(self, request: GenerationRequest, progress, should_cancel) -> GenerationResult:
        progress(0.05, "Submitting Veo job")
        instance: dict[str, Any] = {"prompt": request.prompt}
        if request.reference_images:
            first = request.reference_images[0]
            instance["image"] = {
                "bytesBase64Encoded": base64.b64encode(first.read_bytes()).decode("ascii"),
                "mimeType": _mime_for(first),
            }

        parameters: dict[str, Any] = {
            "aspectRatio": request.options.get("aspect_ratio", "16:9"),
            "durationSeconds": int(request.options.get("duration_seconds", 8)),
            "personGeneration": request.options.get("person_generation", "allow_adult"),
        }
        if request.options.get("resolution"):
            parameters["resolution"] = request.options["resolution"]
        if request.negative_prompt:
            parameters["negativePrompt"] = request.negative_prompt

        operation = request_json(
            "POST", f"{API_ROOT}/models/{request.model_id}:predictLongRunning",
            headers=self._headers(), json_body={"instances": [instance], "parameters": parameters},
            proxy=self._proxy,
        )
        operation_name = operation.get("name")
        if not operation_name:
            raise ProviderError(f"Veo did not return an operation handle: {operation}")

        final = poll_until(
            fetch=lambda: request_json(
                "GET", f"{API_ROOT}/{operation_name}", headers=self._headers(), proxy=self._proxy
            ),
            is_done=lambda payload: bool(payload.get("done")),
            is_failed=lambda payload: (
                str(payload["error"]) if payload.get("error") else None
            ),
            progress=progress,
            label="Veo rendering",
            interval=8.0,
            max_wait=1800.0,
            should_cancel=should_cancel,
        )

        response = final.get("response", {})
        samples = (
            response.get("generateVideoResponse", {}).get("generatedSamples")
            or response.get("generatedSamples")
            or response.get("videos")
            or []
        )
        files: list[Path] = []
        for index, sample in enumerate(samples):
            uri = (
                sample.get("video", {}).get("uri")
                or sample.get("uri")
                or sample.get("gcsUri", "")
            )
            inline = sample.get("video", {}).get("bytesBase64Encoded") or sample.get(
                "bytesBase64Encoded"
            )
            destination = self._stamped_path(request.output_dir, "veo", ".mp4")
            if inline:
                destination.write_bytes(base64.b64decode(inline))
            elif uri:
                progress(0.97, f"Downloading clip {index + 1}")
                download(uri, destination, headers={"x-goog-api-key": self.api_key},
                         proxy=self._proxy)
            else:
                continue
            files.append(destination)

        if not files:
            raise ProviderError(
                "Veo finished but returned no downloadable video. Response was:\n"
                f"{str(response)[:800]}"
            )
        progress(1.0, "Done")
        return GenerationResult(files=files, raw=final, cost_note=f"{len(files)} clip(s)")

    # -- Gemini text / video understanding ------------------------------------------
    def _generate_text(self, request: GenerationRequest, progress) -> GenerationResult:
        progress(0.2, "Calling Gemini")
        parts: list[dict[str, Any]] = [{"text": request.prompt}]

        for image_path in request.reference_images[:6]:
            parts.append({
                "inline_data": {
                    "mime_type": _mime_for(image_path),
                    "data": base64.b64encode(image_path.read_bytes()).decode("ascii"),
                }
            })
        if request.reference_video and request.reference_video.exists():
            progress(0.3, "Uploading video for analysis")
            file_uri = self._upload_file(request.reference_video)
            parts.append({"file_data": {"mime_type": "video/mp4", "file_uri": file_uri}})

        body = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": float(request.options.get("temperature", 0.8)),
            },
        }
        payload = request_json(
            "POST", f"{API_ROOT}/models/{request.model_id}:generateContent",
            headers=self._headers(), json_body=body, proxy=self._proxy, timeout=300,
        )
        text = "".join(
            part.get("text", "")
            for candidate in payload.get("candidates", [])
            for part in candidate.get("content", {}).get("parts", [])
        )
        progress(1.0, "Done")
        return GenerationResult(text=text, raw=payload)

    def _upload_file(self, path: Path) -> str:
        """Resumable upload via the Files API, then waits for ACTIVE state."""
        size = path.stat().st_size
        mime = _mime_for(path)
        start = request_json(
            "POST", f"{API_ROOT}/files",
            headers={
                "x-goog-api-key": self.api_key,
                "X-Goog-Upload-Protocol": "resumable",
                "X-Goog-Upload-Command": "start",
                "X-Goog-Upload-Header-Content-Length": str(size),
                "X-Goog-Upload-Header-Content-Type": mime,
                "Content-Type": "application/json",
            },
            json_body={"file": {"display_name": path.name}},
            proxy=self._proxy,
        )
        # requests hides response headers here, so fall back to the simple multipart route.
        upload_url = start.get("_upload_url") or ""
        if not upload_url:
            return self._upload_simple(path, mime)
        return upload_url

    def _upload_simple(self, path: Path, mime: str) -> str:
        import requests as _requests  # local import: only needed on this path

        if _requests is None:  # pragma: no cover
            raise ProviderError("Video upload needs the `requests` package installed.")
        response = _requests.post(
            "https://generativelanguage.googleapis.com/upload/v1beta/files",
            headers={
                "x-goog-api-key": self.api_key,
                "X-Goog-Upload-Protocol": "raw",
                "Content-Type": mime,
            },
            data=path.read_bytes(),
            timeout=600,
        )
        if response.status_code >= 400:
            raise ProviderError(f"File upload failed: HTTP {response.status_code} {response.text[:400]}")
        file_info = response.json().get("file", {})
        name, uri = file_info.get("name", ""), file_info.get("uri", "")

        # Video files sit in PROCESSING for a while; Gemini rejects them until ACTIVE.
        for _ in range(60):
            status = request_json("GET", f"{API_ROOT}/{name}", headers=self._headers(),
                                  proxy=self._proxy)
            state = status.get("state") or status.get("file", {}).get("state")
            if state == "ACTIVE":
                return uri
            if state == "FAILED":
                raise ProviderError("Google failed to process the uploaded video.")
            time.sleep(3)
        raise ProviderError("Timed out waiting for Google to process the uploaded video.")


def _mime_for(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".webp": "image/webp", ".gif": "image/gif", ".heic": "image/heic",
        ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
        ".mp3": "audio/mpeg", ".wav": "audio/wav",
    }.get(suffix, "application/octet-stream")
