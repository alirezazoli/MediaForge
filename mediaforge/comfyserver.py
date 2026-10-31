"""Starting the local ComfyUI, so "not running" is a link to click rather than a chore.

Generated motion on this PC needs a ComfyUI server. When one is installed but not running,
telling the user to go and start it sends them out of the app to find a launcher; this
starts it the way its own launcher does -- its Python, `main.py`, the configured address --
hidden, with its output in MediaForge's log folder.

A server MediaForge started is stopped again when MediaForge closes. ComfyUI keeps model
weights in memory between runs, and a GPU server nobody remembers starting is how a
laptop ends up with a hot fan and no free VRAM. One the user started is left alone.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

log = logging.getLogger(__name__)

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


@dataclass(frozen=True)
class ComfyInstall:
    root: Path          # the folder holding main.py
    python: Path
    portable: bool      # the Windows portable build, which wants its own flag


def _candidate_roots(folder_hint: str) -> list[Path]:
    home = Path.home()
    roots = []
    if folder_hint:
        hint = Path(folder_hint)
        roots += [hint, hint / "ComfyUI"]
    for base in (home, home / "Documents", home / "Desktop", Path("C:/"), Path("D:/")):
        roots += [base / "ComfyUI", base / "ComfyUI_windows_portable" / "ComfyUI"]
    return roots


def find_install(folder_hint: str = "") -> ComfyInstall | None:
    """A ComfyUI folder with a Python that can run it, or None."""
    for root in _candidate_roots(folder_hint):
        if not (root / "main.py").is_file():
            continue
        for python, portable in ((root / "venv" / "Scripts" / "python.exe", False),
                                 (root / ".venv" / "Scripts" / "python.exe", False),
                                 (root.parent / "python_embeded" / "python.exe", True)):
            if python.is_file():
                return ComfyInstall(root, python, portable)
    return None


def has_wan22_files(install: ComfyInstall) -> bool:
    """The built-in model's three files, checked on disk -- possible while it is stopped."""
    models = install.root / "models"

    def any_file(folders: tuple[str, ...], test: Callable[[str], bool]) -> bool:
        return any(test(entry.name.lower()) for folder in folders
                   if (models / folder).is_dir() for entry in (models / folder).iterdir())

    return (any_file(("diffusion_models", "unet"),
                     lambda name: "ti2v_5b" in name and name.endswith(".safetensors"))
            and any_file(("text_encoders", "clip"), lambda name: "umt5" in name)
            and any_file(("vae",), lambda name: "wan2.2_vae" in name))


def start(install: ComfyInstall, url: str, log_file: Path) -> subprocess.Popen:
    parsed = urlparse(url or "http://127.0.0.1:8188")
    host, port = parsed.hostname or "127.0.0.1", parsed.port or 8188
    argv = [str(install.python), "main.py", "--listen", host, "--port", str(port)]
    if install.portable:
        argv.append("--windows-standalone-build")
    log_file.parent.mkdir(parents=True, exist_ok=True)
    handle = log_file.open("ab")
    log.info("Starting ComfyUI: %s (in %s)", " ".join(argv), install.root)
    environment = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.Popen(argv, cwd=str(install.root), stdout=handle, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, env=environment, creationflags=_NO_WINDOW)


def wait_until_up(url: str, process: subprocess.Popen | None = None, timeout: float = 180.0,
                  progress: Callable[[float, str], None] = lambda fraction, message: None,
                  should_cancel: Callable[[], bool] = lambda: False) -> bool:
    """Waits for the server to answer. False if it exits or the time runs out."""
    import requests

    started = time.time()
    while time.time() - started < timeout:
        if should_cancel():
            return False
        if process is not None and process.poll() is not None:
            return False
        try:
            if requests.get(f"{url.rstrip('/')}/system_stats", timeout=2).status_code == 200:
                return True
        except requests.RequestException:
            pass
        elapsed = time.time() - started
        progress(min(0.95, elapsed / 60.0), f"Starting ComfyUI ({elapsed:.0f} s)")
        time.sleep(1.0)
    return False


def stop(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    log.info("Stopping the ComfyUI this session started (pid %s)", process.pid)
    if sys.platform == "win32":
        # The whole tree. A venv's python.exe on Windows is a launcher that runs the real
        # interpreter as a child -- terminating the launcher leaves ComfyUI itself running
        # and holding the GPU.
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       capture_output=True, creationflags=_NO_WINDOW)
    else:
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
