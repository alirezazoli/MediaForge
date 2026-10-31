"""Settings and credential storage for MediaForge Studio.

Keys live in %APPDATA%/MediaForge/credentials.json, obfuscated with a machine-bound
key. This is *not* a secret manager -- it stops casual shoulder-reading, nothing more.
Anything stronger should come from environment variables, which always win.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


def app_data_dir(respect_override: bool = True) -> Path:
    # An explicit override keeps test runs out of the real profile. Without it every
    # headless run rewrites the user's own settings and learning database -- which is
    # how a test that sets the dark theme quietly changed the theme of the installed app.
    # Callers that want the machine-wide location regardless (installed engines are a
    # multi-gigabyte download, not per-profile state) pass respect_override=False.
    override = os.environ.get("MEDIAFORGE_HOME") if respect_override else None
    if override:
        directory = Path(override) / "MediaForge"
        directory.mkdir(parents=True, exist_ok=True)
        return directory
    if platform.system() == "Windows":
        root = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif platform.system() == "Darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    directory = root / "MediaForge"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def settings_path() -> Path:
    # Resolved on every call, not once at import: an override set after this module was
    # first imported (the self-test sets one) must still be honoured.
    return app_data_dir() / "settings.json"


def credentials_path() -> Path:
    return app_data_dir() / "credentials.json"


def default_workspace() -> Path:
    """Where projects go unless the user picks somewhere else.

    Follows the profile override too. When it did not, an isolated test profile still
    opened the user's real "Untitled Project", and test media ended up in it -- along with
    a database that a stray write from a media library damaged.
    """
    override = os.environ.get("MEDIAFORGE_HOME")
    if override:
        return Path(override) / "MediaForge Projects"
    return Path.home() / "MediaForge Projects"


def _machine_key() -> bytes:
    seed = f"{platform.node()}|{os.environ.get('USERNAME', '')}|mediaforge-v1"
    return hashlib.sha256(seed.encode("utf-8")).digest()


def _xor(data: bytes, key: bytes) -> bytes:
    return bytes(byte ^ key[index % len(key)] for index, byte in enumerate(data))


def _obfuscate(plain: str) -> str:
    return base64.b64encode(_xor(plain.encode("utf-8"), _machine_key())).decode("ascii")


def _deobfuscate(blob: str) -> str:
    try:
        return _xor(base64.b64decode(blob.encode("ascii")), _machine_key()).decode("utf-8")
    except Exception:
        return ""


# Environment variable that overrides the stored value, per credential slot.
CREDENTIAL_SLOTS: dict[str, str] = {
    "google": "GEMINI_API_KEY",
    "google_vertex_project": "GOOGLE_CLOUD_PROJECT",
    "volcengine": "ARK_API_KEY",
    "dashscope": "DASHSCOPE_API_KEY",
    "fal": "FAL_KEY",
    "replicate": "REPLICATE_API_TOKEN",
    "openai_compatible": "OPENAI_API_KEY",
    "elevenlabs": "ELEVENLABS_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "jamendo": "JAMENDO_CLIENT_ID",
    "pixabay": "PIXABAY_API_KEY",
}


class Credentials:
    """Reads env vars first, then the on-disk store."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or credentials_path()
        self._stored: dict[str, str] = {}
        self.reload()

    def reload(self) -> None:
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                self._stored = {slot: _deobfuscate(blob) for slot, blob in raw.items()}
            except (json.JSONDecodeError, OSError):
                self._stored = {}

    def get(self, slot: str) -> str:
        env_name = CREDENTIAL_SLOTS.get(slot)
        if env_name and os.environ.get(env_name):
            return os.environ[env_name].strip()
        return self._stored.get(slot, "").strip()

    def set(self, slot: str, value: str) -> None:
        self._stored[slot] = value.strip()
        self.save()

    def source_of(self, slot: str) -> str:
        env_name = CREDENTIAL_SLOTS.get(slot)
        if env_name and os.environ.get(env_name):
            return f"environment ({env_name})"
        return "stored" if self._stored.get(slot) else "unset"

    def save(self) -> None:
        payload = {slot: _obfuscate(value) for slot, value in self._stored.items() if value}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        if platform.system() != "Windows":
            os.chmod(self.path, 0o600)


@dataclass
class Settings:
    workspace: str = field(default_factory=lambda: str(default_workspace()))
    ffmpeg_path: str = ""          # empty means "look on PATH"
    ffprobe_path: str = ""
    max_parallel_jobs: int = 2
    default_image_provider: str = "google"
    default_video_provider: str = "google"
    default_llm_provider: str = "anthropic"
    comfyui_url: str = "http://127.0.0.1:8188"
    comfyui_folder: str = ""        # blank = look in the usual places
    ollama_url: str = "http://127.0.0.1:11434"
    agent_model: str = ""            # blank = pick the best installed
    agent_autorun_tools: bool = True  # ask before anything costly regardless
    openai_base_url: str = "https://api.openai.com/v1"
    seedance_region: str = "international"   # or "china"
    custom_fal_models: str = ""              # one model id per line
    custom_replicate_models: str = ""        # one owner/name per line
    proxy_url: str = ""
    theme: str = "light"            # Google-style light by default
    learning_enabled: bool = True
    sidebar_order: list[str] = field(default_factory=list)
    sidebar_advanced: bool = False
    sidebar_collapsed: bool = False
    recent_projects: list[str] = field(default_factory=list)

    @classmethod
    def load(cls) -> "Settings":
        path = settings_path()
        if path.exists():
            try:
                data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
                known = {key: data[key] for key in data if key in cls.__annotations__}
                return cls(**known)
            except (json.JSONDecodeError, OSError, TypeError):
                pass
        return cls()

    def save(self) -> None:
        settings_path().write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    @property
    def workspace_path(self) -> Path:
        path = Path(self.workspace)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def remember_project(self, project_dir: str) -> None:
        entries = [project_dir] + [p for p in self.recent_projects if p != project_dir]
        self.recent_projects = entries[:12]
        self.save()
