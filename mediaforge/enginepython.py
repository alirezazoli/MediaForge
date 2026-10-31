"""A separate Python for the pip-installed engines (Whisper, Demucs, rembg, Kokoro).

Inside an installed build `sys.executable` is MediaForge.exe, not Python, so the obvious
`sys.executable -m pip install faster-whisper` would launch a second copy of the app and
install nothing. And the frozen app cannot import a package installed after it was built.

So those engines live in their own virtual environment, created on first use from
whatever Python 3 the machine has, and always run as a child process. That costs a
process start per job and buys two things: the engines work at all in the installed app,
and a crash deep inside a CUDA kernel takes down one job instead of the whole window.

In a source checkout the current interpreter is used directly, so development needs no
extra setup.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

from .config import app_data_dir
from .paths import is_frozen

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

MINIMUM_PYTHON = (3, 10)


class EnginePythonMissing(RuntimeError):
    """No Python 3 on this machine to build the engine environment from."""


def engine_env_dir() -> Path:
    # Machine-level, like the downloaded ncnn engines: an isolated test profile must not
    # trigger a multi-gigabyte reinstall of torch.
    return app_data_dir(respect_override=False) / "engines-python"


def _venv_python(root: Path) -> Path:
    return root / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def engine_python() -> str | None:
    """The interpreter the engines run under, or None if it has not been created yet."""
    if not is_frozen():
        return sys.executable
    candidate = _venv_python(engine_env_dir())
    return str(candidate) if candidate.exists() else None


def site_packages() -> Path | None:
    """Where the engines' packages are installed, for a cheap 'is it installed' check."""
    if not is_frozen():
        return None
    root = engine_env_dir()
    for candidate in (root / "Lib" / "site-packages",
                      *sorted((root / "lib").glob("python3*/site-packages"))):
        if candidate.is_dir():
            return candidate
    return None


def module_installed(module: str) -> bool:
    if not module:
        return False
    if not is_frozen():
        import importlib.util
        try:
            return importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            return False
    packages = site_packages()
    if packages is None:
        return False
    top = module.split(".")[0]
    return (packages / top).is_dir() or (packages / f"{top}.py").exists()


def _is_store_stub(path: str) -> bool:
    """The `python.exe` in WindowsApps opens the Microsoft Store instead of running."""
    return "WindowsApps" in path


def find_system_python() -> str | None:
    """A real Python 3.10+ to build the environment from, or None."""
    candidates: list[str] = []
    launcher = shutil.which("py")
    if launcher:
        result = subprocess.run([launcher, "-3", "-c", "import sys; print(sys.executable)"],
                                capture_output=True, text=True, creationflags=_NO_WINDOW)
        if result.returncode == 0 and result.stdout.strip():
            candidates.append(result.stdout.strip())
    for name in ("python3", "python"):
        found = shutil.which(name)
        if found and not _is_store_stub(found):
            candidates.append(found)
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    candidates += [str(p) for p in sorted((local / "Programs" / "Python").glob("Python3*/python.exe"),
                                          reverse=True)]

    for candidate in candidates:
        check = subprocess.run(
            [candidate, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
            capture_output=True, text=True, creationflags=_NO_WINDOW,
        )
        if check.returncode != 0:
            continue
        try:
            major, minor = (int(part) for part in check.stdout.strip().split("."))
        except ValueError:
            continue
        if (major, minor) >= MINIMUM_PYTHON:
            return candidate
    return None


def ensure_engine_python(progress: Callable[[float, str], None] = lambda f, m: None) -> str:
    """Returns the engine interpreter, creating its environment on first use."""
    existing = engine_python()
    if existing:
        return existing

    base = find_system_python()
    if base is None:
        raise EnginePythonMissing(
            "This engine is a Python package, and the installed app needs a Python 3.10 or "
            "newer on the machine to set it up. Install Python from python.org (tick "
            "'Add to PATH'), then try again. Real-ESRGAN, RIFE and the other GPU engines "
            "do not need this."
        )
    root = engine_env_dir()
    progress(0.05, "Creating the engine environment")
    created = subprocess.run([base, "-m", "venv", str(root)], capture_output=True, text=True,
                             creationflags=_NO_WINDOW)
    python = _venv_python(root)
    if created.returncode != 0 or not python.exists():
        raise RuntimeError("Could not create the engine environment:\n"
                           + (created.stderr or created.stdout)[-600:])
    subprocess.run([str(python), "-m", "pip", "install", "-q", "--upgrade", "pip"],
                   capture_output=True, creationflags=_NO_WINDOW)
    return str(python)
