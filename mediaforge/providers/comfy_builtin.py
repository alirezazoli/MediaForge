"""Graphs MediaForge sends to ComfyUI itself, so a model runs with no workflow to export.

The ComfyUI provider used to run only a graph the user had exported by hand. That is a
reasonable escape hatch and a poor default: someone with ComfyUI and the Wan 2.2 weights
already installed still got "needs a workflow" and a camera move instead of generated
motion. This module builds the Wan 2.2 TI2V 5B graph directly -- one model that does both
text-to-video and image-to-video -- checks the model files are really on the server, and
follows the sampler's progress over ComfyUI's websocket.

Measured on an RTX 2000 Ada laptop GPU (8 GB) with 32 GB RAM, ComfyUI 0.42, 20 steps:
640x352 x 49 frames 77 s from cold; 960x544 x 81 frames 264-278 s warm. The weights are
10 GB in fp16, so ComfyUI offloads part of them; the timings include that.

What the model does with a still photograph, measured on an architectural render: the
geometry holds and nothing is invented when the prompt asks for that, the scene gains
natural motion (foliage, light), and the camera itself barely moves (no global shift
from first to last frame). So the clip is reported as a static-camera clip, and the photo
film adds its own camera move on top when it conforms the clip.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from .base import ProviderError
from .http import request_json

WAN22_ID = "wan2.2-ti2v-5b"
WAN22_FPS = 24
MAX_FRAMES = 81                  # 3.4 s; longer shots are stretched when conformed

# Without these, a still with a loose prompt grows clouds and aircraft (seen in testing).
DEFAULT_NEGATIVE = (
    "new objects appearing, people appearing, cars, birds, aircraft, morphing, warping, "
    "melting, changing architecture, extra buildings, text, watermark, logo, flicker, "
    "jitter, blurry, distorted, cartoon, low quality, static frozen frame"
)

# (width, height) per quality, landscape. Multiples of 32, as the Wan VAE requires.
SIZES = {
    "Draft": (640, 352),
    "Standard": (960, 544),
    "High (slow)": (1280, 704),
}
STEPS = {"Draft": 14, "Standard": 20, "High (slow)": 24}


# Seconds per output frame, measured here (see the module docstring). Replaced by this
# machine's own numbers after its first run.
DEFAULT_SECONDS_PER_FRAME = {"Draft": 1.6, "Standard": 3.4, "High (slow)": 8.0}


def _timing_file() -> Path:
    from ..config import app_data_dir

    # Machine-level, like the engines: it describes the GPU, not a profile.
    return app_data_dir(respect_override=False) / "comfy_timing.json"


def _load_timing() -> dict[str, float]:
    try:
        return json.loads(_timing_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def estimate_seconds(quality: str, frames: int) -> float:
    per_frame = _load_timing().get(quality, DEFAULT_SECONDS_PER_FRAME.get(quality, 3.4))
    return per_frame * frames


def record_timing(quality: str, frames: int, seconds: float) -> None:
    """Folds one real run into the estimate, so the next one is quoted from this GPU."""
    if frames <= 0 or seconds <= 0:
        return
    timing = _load_timing()
    measured = seconds / frames
    previous = timing.get(quality)
    timing[quality] = round(measured if previous is None else 0.6 * previous + 0.4 * measured, 3)
    try:
        _timing_file().write_text(json.dumps(timing, indent=2), encoding="utf-8")
    except OSError:
        pass


@dataclass(frozen=True)
class WanFiles:
    unet: str
    text_encoder: str
    vae: str


_files_cache: dict[str, tuple[float, WanFiles | None]] = {}
FILES_TTL = 30.0


def _choices(base_url: str, node: str, field: str) -> list[str]:
    info = request_json("GET", f"{base_url}/object_info/{node}", timeout=10)
    spec = info.get(node, {}).get("input", {}).get("required", {}).get(field, [[]])
    values = spec[0] if spec else []
    return [value for value in values if isinstance(value, str)]


def find_wan22_files(base_url: str, force: bool = False) -> WanFiles | None:
    """The three Wan 2.2 5B files as the server names them, or None if any is missing."""
    now = time.time()
    cached = _files_cache.get(base_url)
    if cached and not force and now - cached[0] < FILES_TTL:
        return cached[1]
    found: WanFiles | None = None
    try:
        unets = [name for name in _choices(base_url, "UNETLoader", "unet_name")
                 if "ti2v_5b" in name.lower().replace("-", "_") and name.endswith(".safetensors")]
        encoders = [name for name in _choices(base_url, "CLIPLoader", "clip_name")
                    if "umt5" in name.lower()]
        vaes = [name for name in _choices(base_url, "VAELoader", "vae_name")
                if "wan2.2_vae" in name.lower() or "wan2_2_vae" in name.lower()]
        if unets and encoders and vaes:
            # fp16 first: on this class of GPU the fp8 variants are not faster, only worse.
            unets.sort(key=lambda name: ("fp16" not in name, name))
            found = WanFiles(unets[0], encoders[0], vaes[0])
    except (ProviderError, OSError, ValueError):
        found = None
    _files_cache[base_url] = (now, found)
    return found


def frames_for(seconds: float) -> int:
    """Wan wants 4k+1 frames. Capped: the time cost grows faster than the length."""
    wanted = max(17, int(round(seconds * WAN22_FPS)))
    return min(MAX_FRAMES, ((wanted - 1 + 3) // 4) * 4 + 1)


def size_for(aspect_width: int, aspect_height: int, quality: str) -> tuple[int, int]:
    long_side, short_side = SIZES.get(quality, SIZES["Standard"])
    if aspect_height > aspect_width:
        return short_side, long_side
    if aspect_height == aspect_width:
        side = ((long_side + short_side) // 2 // 32) * 32
        return side, side
    return long_side, short_side


def wan22_graph(files: WanFiles, prompt: str, negative: str, image_name: str | None,
                width: int, height: int, length: int, steps: int, cfg: float, seed: int,
                prefix: str, single_file: bool) -> dict[str, Any]:
    graph: dict[str, Any] = {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": files.unet, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": files.text_encoder, "type": "wan"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": files.vae}},
        "4": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["1", 0], "shift": 8.0}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": negative}},
        "8": {"class_type": "Wan22ImageToVideoLatent",
              "inputs": {"vae": ["3", 0], "width": width, "height": height,
                         "length": length, "batch_size": 1}},
        "9": {"class_type": "KSampler",
              "inputs": {"model": ["4", 0], "seed": seed, "steps": steps, "cfg": cfg,
                         "sampler_name": "uni_pc", "scheduler": "simple",
                         "positive": ["5", 0], "negative": ["6", 0],
                         "latent_image": ["8", 0], "denoise": 1.0}},
        "10": {"class_type": "VAEDecode", "inputs": {"samples": ["9", 0], "vae": ["3", 0]}},
    }
    if image_name:
        graph["7"] = {"class_type": "LoadImage", "inputs": {"image": image_name}}
        graph["8"]["inputs"]["start_image"] = ["7", 0]
    if single_file:
        graph["11"] = {"class_type": "SaveWEBM",
                       "inputs": {"images": ["10", 0], "filename_prefix": prefix,
                                  "codec": "vp9", "fps": float(WAN22_FPS), "crf": 12.0}}
    else:
        graph["11"] = {"class_type": "SaveImage",
                       "inputs": {"images": ["10", 0], "filename_prefix": prefix}}
    return graph


def has_node(base_url: str, node: str) -> bool:
    try:
        return node in request_json("GET", f"{base_url}/object_info/{node}", timeout=10)
    except (ProviderError, OSError):
        return False


def upload_image(base_url: str, path: Path) -> str:
    """Puts a photo in ComfyUI's input folder and returns the name to load it by."""
    import requests

    name = f"mediaforge_{uuid.uuid4().hex[:10]}{path.suffix.lower() or '.png'}"
    with path.open("rb") as handle:
        response = requests.post(f"{base_url}/upload/image",
                                 files={"image": (name, handle, "application/octet-stream")},
                                 data={"overwrite": "true"}, timeout=60)
    if response.status_code >= 400:
        raise ProviderError(f"ComfyUI refused the photo upload ({response.status_code}).")
    return response.json().get("name", name)


# ======================================================================================
# Progress over ComfyUI's websocket
# ======================================================================================
class ProgressSocket(threading.Thread):
    """Reads ComfyUI's websocket for one client id and reports sampler steps.

    HTTP polling only says "not finished yet" for four minutes. The websocket carries
    `progress` messages per sampling step, which is the only honest progress there is.
    A minimal RFC 6455 reader: ComfyUI's frames are unmasked text or binary, and nothing
    has to be sent after the handshake, so no websocket library is needed.
    """

    def __init__(self, base_url: str, client_id: str,
                 on_step: Callable[[int, int, str], None]) -> None:
        super().__init__(daemon=True, name="comfyui-progress")
        self.base_url = base_url
        self.client_id = client_id
        self.on_step = on_step
        self.prompt_id = ""
        self.stopping = threading.Event()
        self.connected = threading.Event()

    def stop(self) -> None:
        self.stopping.set()

    def _read_exact(self, sock: socket.socket, count: int) -> bytes:
        data = b""
        while len(data) < count:
            try:
                chunk = sock.recv(count - len(data))
            except socket.timeout:
                if self.stopping.is_set():
                    raise ConnectionAbortedError
                continue
            if not chunk:
                raise ConnectionResetError
            data += chunk
        return data

    def run(self) -> None:
        parsed = urlparse(self.base_url)
        host, port = parsed.hostname or "127.0.0.1", parsed.port or 80
        try:
            sock = socket.create_connection((host, port), timeout=5)
        except OSError:
            return
        try:
            key = base64.b64encode(os.urandom(16)).decode()
            sock.sendall((
                f"GET /ws?clientId={self.client_id} HTTP/1.1\r\nHost: {host}:{port}\r\n"
                "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
            ).encode())
            header = b""
            while b"\r\n\r\n" not in header:
                header += self._read_exact(sock, 1)
            if b" 101 " not in header.split(b"\r\n", 1)[0]:
                return
            self.connected.set()
            sock.settimeout(0.5)
            while not self.stopping.is_set():
                first, second = self._read_exact(sock, 2)
                opcode, length = first & 0x0F, second & 0x7F
                if length == 126:
                    length = int.from_bytes(self._read_exact(sock, 2), "big")
                elif length == 127:
                    length = int.from_bytes(self._read_exact(sock, 8), "big")
                payload = self._read_exact(sock, length) if length else b""
                if opcode == 8:                      # close
                    return
                if opcode != 1:                      # previews are binary; ignore
                    continue
                try:
                    message = json.loads(payload.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                data = message.get("data", {}) or {}
                if self.prompt_id and data.get("prompt_id") not in (None, self.prompt_id):
                    continue
                if message.get("type") == "progress":
                    self.on_step(int(data.get("value", 0)), int(data.get("max", 1) or 1),
                                 str(data.get("node", "")))
                elif message.get("type") == "executing" and data.get("node"):
                    self.on_step(-1, 0, str(data.get("node")))
        except (OSError, ConnectionAbortedError, ConnectionResetError, ValueError):
            return
        finally:
            sock.close()
