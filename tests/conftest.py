"""Shared fixtures.

Every test runs against a throwaway profile (MEDIAFORGE_HOME), so a test run can never
rewrite the real settings, learning database or catalogues of the person running it.
Media fixtures are generated with ffmpeg rather than checked in: they are exact about
what is in them (two seconds of black and silence, then picture and tone), and the
repository stays small.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

os.environ["MEDIAFORGE_HOME"] = tempfile.mkdtemp(prefix="mediaforge_tests_")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mediaforge.config import default_workspace  # noqa: E402
from mediaforge.ffmpeg_tools import FfmpegLocator  # noqa: E402

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# The guard that matters most: no test may ever open a project in the real workspace.
assert str(default_workspace()).startswith(os.environ["MEDIAFORGE_HOME"]), default_workspace()


@pytest.fixture(scope="session")
def locator() -> FfmpegLocator:
    found = FfmpegLocator()
    if not found.available:
        pytest.skip("ffmpeg is not installed")
    return found


@pytest.fixture(scope="session")
def media_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix="mediaforge_media_"))


def _ffmpeg(locator: FfmpegLocator, *args: str) -> None:
    subprocess.run([locator.ffmpeg, "-y", "-v", "error", *args], check=True,
                   capture_output=True, creationflags=_NO_WINDOW)


@pytest.fixture(scope="session")
def plain_clip(locator, media_dir) -> Path:
    """Four seconds of moving test pattern with a tone. Nothing wrong with it."""
    path = media_dir / "plain.mp4"
    _ffmpeg(locator, "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=4",
            "-f", "lavfi", "-i", "sine=frequency=330:duration=4",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path))
    return path


@pytest.fixture(scope="session")
def faulty_clip(locator, media_dir) -> Path:
    """Two seconds of black silence, then three seconds of picture and tone."""
    path = media_dir / "faulty.mp4"
    _ffmpeg(locator,
            "-f", "lavfi", "-i", "color=black:size=320x240:rate=25:duration=2",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:duration=2",
            "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=3",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
            "-filter_complex", "[0:v][1:a][2:v][3:a]concat=n=2:v=1:a=1[v][a]",
            "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", str(path))
    return path


@pytest.fixture(scope="session")
def photos(media_dir) -> list[Path]:
    from PIL import Image, ImageDraw

    made = []
    for index, colour in enumerate([(40, 70, 110), (120, 80, 50), (60, 110, 70)]):
        path = media_dir / f"photo_{index}.jpg"
        image = Image.new("RGB", (1200, 800), colour)
        ImageDraw.Draw(image).rectangle([200, 200, 700 + index * 80, 600], fill=(235, 235, 230))
        image.save(path, quality=90)
        made.append(path)
    return made


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    # The offscreen platform ships no fonts: text falls back to a face about 2.4x wider
    # than Segoe UI, so every width a test measured was wrong. Load the real ones.
    fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for name in ("segoeui.ttf", "segoeuib.ttf", "seguisb.ttf", "SegUIVar.ttf",
                 "SegoeIcons.ttf"):
        if (fonts / name).exists():
            QFontDatabase.addApplicationFont(str(fonts / name))
    return app


@pytest.fixture()
def window(qapp):
    """The real main window on a fresh context, closed and saved afterwards."""
    from mediaforge.context import AppContext
    from mediaforge.ui.main_window import MainWindow

    context = AppContext()
    main = MainWindow(context)
    main.resize(1280, 820)
    main.show()
    qapp.processEvents()
    yield main
    context.shutdown()
    main.deleteLater()
    qapp.processEvents()


def wait_until(qapp, condition, timeout: float = 60.0) -> bool:
    """Pumps the event loop until `condition()` holds, so queued job signals arrive."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if condition():
            return True
        time.sleep(0.02)
    return False
