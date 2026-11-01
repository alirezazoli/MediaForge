"""Ollama: free local models, including tool calling.

Ollama is the practical way to give this app a brain that costs nothing and works
offline. Its /api/chat endpoint speaks tool calling for models that support it, which is
what the agent needs; models that do not are still usable for plain conversation.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .base import (
    Capability, GenerationResult, ModelSpec, Provider, ProviderError,
)
from .http import request_json

DEFAULT_URL = "http://127.0.0.1:11434"
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

# Families known to handle tool calling well. Used only as a hint when the server is too
# old to report capabilities itself.
TOOL_CAPABLE_HINTS = (
    "llama3.1", "llama3.2", "llama3.3", "llama4", "qwen2.5", "qwen3", "mistral",
    "mixtral", "command-r", "firefunction", "hermes", "granite", "kimi", "gpt-oss",
)

# Ollama advertising `tools` is not the same as a model using them properly. Reasoning
# models in particular tend to *describe* a call in prose and then invent its result,
# which is worse than not calling at all. Rank by observed reliability, best first.
TOOL_QUALITY = (
    ("llama3.3", 100), ("llama3.2", 95), ("llama3.1", 92), ("llama4", 96),
    ("qwen3-coder", 70), ("qwen3", 88), ("qwen2.5", 86),
    ("mistral", 84), ("mixtral", 82), ("command-r", 86), ("firefunction", 90),
    ("hermes", 80), ("granite", 78), ("gpt-oss", 88), ("kimi", 75),
    ("gemma", 55),
    # These advertise tools but reliably emit pseudo-calls as text instead.
    ("deepseek-r1", 10), ("-r1", 10), ("reasoner", 10), ("deepseek-coder", 15),
)


def tool_reliability(name: str) -> int:
    lowered = name.lower()
    for needle, score in TOOL_QUALITY:
        if needle in lowered:
            return score
    return 50


@dataclass
class OllamaModel:
    name: str
    size_bytes: int = 0
    families: tuple[str, ...] = ()
    supports_tools: bool = False
    supports_vision: bool = False
    supports_thinking: bool = False
    is_cloud: bool = False

    @property
    def tool_reliability(self) -> int:
        return tool_reliability(self.name) if self.supports_tools else 0

    @property
    def unreliable_tools(self) -> bool:
        """Advertises tool support but tends to fake calls in prose."""
        return self.supports_tools and self.tool_reliability < 40

    @property
    def pretty_size(self) -> str:
        if self.is_cloud:
            return "cloud"
        gigabytes = self.size_bytes / (1024 ** 3)
        return f"{gigabytes:.1f} GB" if gigabytes >= 0.1 else "-"

    def label(self) -> str:
        marks = []
        if self.unreliable_tools:
            marks.append("tools: unreliable")
        elif self.supports_tools:
            marks.append("tools")
        if self.supports_vision:
            marks.append("vision")
        if self.is_cloud:
            marks.append("cloud")
        suffix = f"  [{', '.join(marks)}]" if marks else ""
        return f"{self.name}  ({self.pretty_size}){suffix}"


class OllamaProvider(Provider):
    key = "ollama"
    name = "Ollama (local models)"
    homepage = "https://ollama.com/download"
    credential_slot = ""
    credential_help = (
        "No key. Install Ollama, then pull a model, e.g.  ollama pull llama3.1:8b"
    )

    TEMPERATURE = {"temperature": {"type": "float", "default": 0.7, "min": 0.0, "max": 2.0}}

    @property
    def models(self) -> list[ModelSpec]:
        """"Best installed" plus every model pulled on this machine, each by name.

        Only one "auto" entry used to be offered, so a writer who wanted the bigger model
        for a script, or the small fast one for captions, could not choose. Models that see
        images also read videos (as frames). Cloud-routed models are left out: they need an
        Ollama account and a connection, which is not what "local" promises.
        """
        installed = [model for model in self.list_models() if not model.is_cloud]
        sees = any(model.supports_vision for model in installed)
        specs = [ModelSpec(
            id="auto", label="Best installed local model",
            capabilities=[Capability.LLM] + ([Capability.VIDEO_UNDERSTANDING] if sees else []),
            notes="Runs entirely on your machine: offline, unlimited, no key.",
            options=dict(self.TEMPERATURE),
        )]
        for model in installed:
            notes = [f"{model.pretty_size} on disk, runs on this PC."]
            if model.supports_vision:
                notes.append("Reads images, so it can also describe a video from its frames.")
            if model.unreliable_tools:
                notes.append("Writes well, but not reliable for the Assistant's actions.")
            specs.append(ModelSpec(
                id=model.name, label=f"{model.name} ({model.pretty_size})",
                capabilities=[Capability.LLM] + ([Capability.VIDEO_UNDERSTANDING]
                                                 if model.supports_vision else []),
                notes=" ".join(notes), options=dict(self.TEMPERATURE),
            ))
        return specs

    _reach_cache: tuple[float, bool] | None = None
    _model_cache: tuple[float, list[OllamaModel]] | None = None
    REACH_TTL = 15.0
    MODEL_TTL = 30.0

    # -- server ---------------------------------------------------------------------------
    @property
    def base_url(self) -> str:
        return (getattr(self.settings, "ollama_url", "") or DEFAULT_URL).rstrip("/")

    @staticmethod
    def executable() -> str:
        found = shutil.which("ollama")
        if found:
            return found
        candidates = [
            Path.home() / "AppData/Local/Programs/Ollama/ollama.exe",
            Path("C:/Program Files/Ollama/ollama.exe"),
            Path("/usr/local/bin/ollama"),
            Path("/opt/homebrew/bin/ollama"),
        ]
        return next((str(path) for path in candidates if path.exists()), "")

    @property
    def installed(self) -> bool:
        return bool(self.executable())

    def reachable(self, force: bool = False) -> bool:
        now = time.time()
        cached = type(self)._reach_cache
        if not force and cached and now - cached[0] < self.REACH_TTL:
            return cached[1]

        alive = False
        try:
            from urllib.parse import urlparse

            parsed = urlparse(self.base_url)
            with socket.create_connection(
                (parsed.hostname or "127.0.0.1", parsed.port or 11434), timeout=0.6
            ):
                alive = True
        except OSError:
            alive = False
        type(self)._reach_cache = (now, alive)
        return alive

    def start_server(self, wait_seconds: float = 12.0) -> bool:
        """Launches `ollama serve` in the background. Returns True once it answers."""
        executable = self.executable()
        if not executable:
            return False
        if self.reachable(force=True):
            return True
        try:
            subprocess.Popen(
                [executable, "serve"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=_NO_WINDOW,
            )
        except OSError:
            return False

        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            if self.reachable(force=True):
                return True
            time.sleep(0.4)
        return False

    # -- models ----------------------------------------------------------------------------
    def list_models(self, force: bool = False) -> list[OllamaModel]:
        now = time.time()
        cached = type(self)._model_cache
        if not force and cached and now - cached[0] < self.MODEL_TTL:
            return cached[1]
        if not self.reachable():
            type(self)._model_cache = (now, [])
            return []

        try:
            payload = request_json("GET", f"{self.base_url}/api/tags", timeout=8)
        except ProviderError:
            type(self)._model_cache = (now, [])
            return []

        found: list[OllamaModel] = []
        for entry in payload.get("models", []):
            name = entry.get("name") or entry.get("model") or ""
            if not name:
                continue
            details = entry.get("details") or {}
            families = tuple(details.get("families") or
                             ([details["family"]] if details.get("family") else []))
            model = OllamaModel(
                name=name,
                size_bytes=int(entry.get("size") or 0),
                families=families,
                is_cloud=name.endswith(":cloud") or not entry.get("size"),
            )
            self._fill_capabilities(model)
            found.append(model)

        found.sort(key=lambda item: (not item.supports_tools,
                                     -tool_reliability(item.name), item.name))
        type(self)._model_cache = (now, found)
        return found

    def _fill_capabilities(self, model: OllamaModel) -> None:
        """Asks the server what a model can do, falling back to a name heuristic.

        `/api/show` reports capabilities on recent Ollama versions. Guessing from the
        name alone would wrongly rule out models the server actually supports.
        """
        try:
            info = request_json(
                "POST", f"{self.base_url}/api/show", json_body={"model": model.name}, timeout=8
            )
            capabilities = {str(item).lower() for item in info.get("capabilities", [])}
            if capabilities:
                model.supports_tools = "tools" in capabilities
                model.supports_vision = "vision" in capabilities
                model.supports_thinking = "thinking" in capabilities
                return
            template = str(info.get("template", "")).lower()
            if ".tools" in template or "tool_calls" in template:
                model.supports_tools = True
                return
        except ProviderError:
            pass

        lowered = model.name.lower()
        model.supports_tools = any(hint in lowered for hint in TOOL_CAPABLE_HINTS)
        model.supports_vision = any(
            hint in lowered for hint in ("llava", "vision", "gemma3", "gemma4", "qwen2.5vl")
        )

    def best_model(self, need_tools: bool = True) -> OllamaModel | None:
        models = self.list_models()
        if not models:
            return None
        if need_tools:
            usable = [m for m in models if m.supports_tools]
            if usable:
                # Prefer a real local model over a cloud-routed one: free and offline.
                local = [m for m in usable if not m.is_cloud]
                return (local or usable)[0]
        return models[0]

    # Small vision models first: on an 8 GB laptop GPU a 4B model reads six frames in
    # seconds, while a 12B one spills into system memory.
    VISION_PREFERENCE = ("qwen3.5", "qwen2.5vl", "gemma4", "gemma3", "llava")

    def best_vision_model(self) -> OllamaModel | None:
        seeing = [m for m in self.list_models() if m.supports_vision and not m.is_cloud]

        def rank(model: OllamaModel) -> tuple[int, int]:
            family = next((index for index, needle in enumerate(self.VISION_PREFERENCE)
                           if needle in model.name.lower()), len(self.VISION_PREFERENCE))
            return family, model.size_bytes

        return min(seeing, key=rank) if seeing else None

    def _model_info(self, name: str) -> OllamaModel | None:
        return next((model for model in self.list_models() if model.name == name), None)

    def pull(self, name: str, progress: Callable[[float, str], None] = lambda f, m: None) -> bool:
        """Downloads a model, streaming progress. Blocking; run it in a job."""
        executable = self.executable()
        if not executable:
            raise ProviderError("Ollama is not installed.")
        process = subprocess.Popen(
            [executable, "pull", name],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", creationflags=_NO_WINDOW,
        )
        assert process.stdout is not None
        for line in process.stdout:
            line = line.strip()
            if line:
                progress(0.5, line[:90])
        process.wait()
        type(self)._model_cache = None
        return process.returncode == 0

    # -- chat --------------------------------------------------------------------------------
    def chat(
        self,
        messages: list[dict],
        model: str = "",
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        timeout: int = 300,
        should_cancel: Callable[[], bool] | None = None,
        think: bool | None = None,
    ) -> dict:
        """One turn. Returns the assistant message dict, including any tool_calls.

        `think=False` asks a reasoning model to answer straight away. Sent only to models
        that report thinking; others reject the field.
        """
        if not self.reachable():
            raise ProviderError(
                f"Ollama is not running at {self.base_url}. Start it from the Agent "
                f"screen, or run `ollama serve`."
            )
        if not model or model == "auto":
            chosen = self.best_model(need_tools=bool(tools))
            if chosen is None:
                raise ProviderError(
                    "No Ollama models are installed. Pull one first, for example:\n"
                    "    ollama pull llama3.1:8b"
                )
            model = chosen.name

        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature},
        }
        if tools:
            body["tools"] = tools
        info = self._model_info(model)
        if think is not None and info is not None and info.supports_thinking:
            body["think"] = think

        payload = request_json(
            "POST", f"{self.base_url}/api/chat", json_body=body, timeout=timeout
        )
        message = payload.get("message") or {}
        if not message:
            raise ProviderError(f"Ollama returned no message: {str(payload)[:400]}")
        message.setdefault("role", "assistant")
        message.setdefault("content", "")
        return message

    # -- Provider interface --------------------------------------------------------------------
    @property
    def configured(self) -> bool:
        return self.reachable()

    def generate(self, request, progress=lambda f, m: None, should_cancel=None) -> GenerationResult:
        started = time.time()
        if request.capability == Capability.VIDEO_UNDERSTANDING:
            return self._understand(request, progress, should_cancel)
        progress(0.2, "Asking the local model")
        message = self.chat(
            [{"role": "user", "content": request.prompt}],
            model=request.model_id if request.model_id != "auto" else "",
            temperature=float(request.options.get("temperature", 0.7)),
            should_cancel=should_cancel, think=False,
        )
        progress(1.0, "Done")
        return GenerationResult(
            text=message.get("content", ""), provider=self.key,
            model_id=request.model_id, elapsed_seconds=time.time() - started,
        )

    FRAMES_TO_READ = 6

    def _understand(self, request, progress, should_cancel) -> GenerationResult:
        """A video, as evenly spaced frames, put to a model that reads images."""
        import base64

        started = time.time()
        if request.model_id and request.model_id != "auto":
            model = request.model_id
        else:
            chosen = self.best_vision_model()
            if chosen is None:
                raise ProviderError("No installed Ollama model reads images. Pull one, for "
                                    "example:  ollama pull qwen3.5:4b")
            model = chosen.name
        progress(0.1, "Taking frames from the video")
        if request.reference_video:
            frames = sample_frames(Path(request.reference_video), self.FRAMES_TO_READ)
        elif request.reference_images:
            frames = [Path(path) for path in request.reference_images]
        else:
            raise ProviderError("Choose the video (or images) to ask about.")
        images = [base64.b64encode(path.read_bytes()).decode("ascii") for path in frames]
        question = request.prompt.strip() or (
            "Describe what happens in this video: the setting, the subjects, the action and "
            "how the camera moves.")
        progress(0.3, f"{model} is watching {len(images)} frames")
        message = self.chat(
            [{"role": "user", "content": (
                f"These are {len(images)} frames taken in order from one video. "
                f"{question}"), "images": images}],
            model=model, temperature=float(request.options.get("temperature", 0.3)),
            should_cancel=should_cancel, think=False, timeout=600,
        )
        progress(1.0, "Done")
        return GenerationResult(
            text=message.get("content", ""), provider=self.key, model_id=model,
            elapsed_seconds=time.time() - started, raw={"frames": len(images)},
        )


def sample_frames(video: Path, count: int, width: int = 768) -> list[Path]:
    """`count` frames spread over the whole clip, as JPEGs in a temporary folder."""
    import tempfile

    from ..ffmpeg_tools import FfmpegLocator, probe

    locator = FfmpegLocator()
    locator.require()
    duration = probe(locator, video).duration or 0.0
    folder = Path(tempfile.mkdtemp(prefix="mediaforge_frames_"))
    frames = []
    for index in range(count):
        at = duration * (index + 0.5) / count if duration else 0.0
        target = folder / f"frame_{index:02d}.jpg"
        subprocess.run(
            [locator.ffmpeg, "-y", "-v", "error", "-ss", f"{at:.3f}", "-i", str(video),
             "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "3", str(target)],
            capture_output=True, creationflags=_NO_WINDOW)
        if target.exists():
            frames.append(target)
    if not frames:
        raise ProviderError(f"Could not read any frames from {video.name}.")
    return frames
