"""Where the application's own files are, in source checkouts and in installed builds.

A PyInstaller build moves everything: modules are unpacked under `_internal/`, data files
sit next to them, and `sys.executable` is the app itself rather than python.exe. Code
that builds paths from `__file__` or assumes a `python` binary works in development and
fails only after installation, where nobody can see a traceback. Every path the app needs
at runtime goes through here instead.
"""

from __future__ import annotations

import sys
from pathlib import Path

APP_NAME = "MediaForge Studio"
APP_ID = "MediaForge.Studio"          # Windows AppUserModelID; groups taskbar icons
__version__ = "1.0.1"


def is_frozen() -> bool:
    """True inside a PyInstaller build."""
    return bool(getattr(sys, "frozen", False))


def install_dir() -> Path:
    """The folder holding MediaForge.exe when installed, or the project root in a checkout."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_dir() -> Path:
    """Where bundled read-only data was unpacked (`_internal` in a onedir build)."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", install_dir()))
    return Path(__file__).resolve().parent.parent


def bundled_ffmpeg_dir() -> Path:
    """The ffmpeg shipped inside the installer.

    Kept beside the executable rather than inside `_internal`, so a user who wants to swap
    in a newer build can see it and replace it without unpacking anything.
    """
    return install_dir() / "ffmpeg"


def asset_path(name: str) -> Path:
    """A file from mediaforge/assets, or an empty Path if the build left it out."""
    for candidate in (resource_dir() / "mediaforge" / "assets" / name,
                      Path(__file__).resolve().parent / "assets" / name):
        if candidate.exists():
            return candidate
    return Path()


def icon_path() -> Path:
    return asset_path("mediaforge.ico")
