"""`MediaForge.exe --selftest`: proves an installed copy can actually do its job.

A build that starts is not a build that works. PyInstaller silently leaves out anything it
cannot see an import for -- a Qt plugin, a data file, numpy's C extensions -- and the gap
only shows when that code path runs. This exercises the real paths end to end, headless:
find ffmpeg, load the catalogues, render a short film from generated photos, measure a
clip with known faults, and build the main window. The result is written as JSON, and
the exit code is the number of failed checks.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Callable

from .paths import APP_NAME, __version__, install_dir, is_frozen


def _make_photo(path: Path, colour: tuple[int, int, int], pattern: int) -> None:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (1280, 860), colour)
    draw = ImageDraw.Draw(image)
    for step in range(0, 1280, 80):
        draw.rectangle([step, 300 + (step * pattern) % 200, step + 40, 760], fill=(240, 240, 235))
    draw.ellipse([900, 80, 1100, 280], fill=(255, 210, 120))
    image.save(path, quality=92)


def run(report_path: Path | None = None) -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    work = Path(tempfile.mkdtemp(prefix="mediaforge_selftest_"))
    # A throwaway profile: building the main window saves settings on shutdown, and a
    # check run on someone's machine must not reset their theme or last project.
    os.environ["MEDIAFORGE_HOME"] = str(work / "profile")
    results: list[dict] = []

    def check(name: str, action: Callable[[], str]) -> None:
        started = time.perf_counter()
        try:
            detail = action()
            results.append({"check": name, "ok": True, "detail": detail,
                            "seconds": round(time.perf_counter() - started, 2)})
        except Exception as error:      # every failure is the result, not a crash
            results.append({"check": name, "ok": False, "detail": f"{type(error).__name__}: {error}",
                            "trace": traceback.format_exc()[-1500:],
                            "seconds": round(time.perf_counter() - started, 2)})

    from .ffmpeg_tools import FfmpegLocator, probe

    locator = FfmpegLocator()

    def ffmpeg_present() -> str:
        locator.require()
        return f"{locator.version()} at {locator.ffmpeg}"
    check("ffmpeg is present", ffmpeg_present)

    def ffmpeg_filters() -> str:
        from .ffmpeg_extra import available_encoders
        found = available_encoders(locator)
        missing = [name for name in ("vidstab", "drawtext", "deband") if not found.get(name)]
        if missing:
            raise RuntimeError(f"this ffmpeg lacks: {', '.join(missing)}")
        return "vidstab, drawtext, deband available"
    check("ffmpeg has the filters the app uses", ffmpeg_filters)

    def catalogues() -> str:
        from .catalog import load_local_tools, load_models
        models = load_models().get("models", [])
        tools = load_local_tools().get("tools", [])
        if not models or not tools:
            raise RuntimeError("a bundled catalogue is empty or missing")
        return f"{len(models)} models, {len(tools)} local engines"
    check("bundled catalogues load", catalogues)

    def photo_film() -> str:
        from .photofilm import PhotoFilmSettings, Shot, render
        photos = []
        for index, colour in enumerate([(40, 70, 110), (120, 80, 50)]):
            path = work / f"photo_{index}.jpg"
            _make_photo(path, colour, index + 1)
            photos.append(path)
        settings = PhotoFilmSettings(aspect="16:9 landscape", seconds_each=1.5, crossfade=0.4,
                                     fps=24)
        output = work / "film.mp4"
        render(locator, [Shot(path=p, seconds=1.5) for p in photos], output, settings,
               work / "shots")
        info = probe(locator, output)
        if info.duration < 2.0 or (info.width, info.height) != settings.size:
            raise RuntimeError(f"unexpected output {info.resolution} {info.duration:.2f}s")
        return f"{info.resolution}, {info.duration:.1f}s"
    check("photos render into a film", photo_film)

    def repair_scan() -> str:
        import subprocess
        from . import repair
        clip = work / "faulty.mp4"
        subprocess.run([
            locator.ffmpeg, "-y", "-v", "error",
            "-f", "lavfi", "-i", "color=black:size=320x240:rate=25:duration=2",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:duration=2",
            "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=3",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
            "-filter_complex", "[0:v][1:a][2:v][3:a]concat=n=2:v=1:a=1[v][a]",
            "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", str(clip)], check=True, capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        report = repair.scan(locator, clip)
        if len(report.dead) != 1:
            raise RuntimeError(f"expected one dead section, found {len(report.dead)}")
        if report.measurement.samples == 0:
            raise RuntimeError("no frames could be measured (numpy missing?)")
        return f"found {report.dead_seconds:.1f}s dead air; noise {report.measurement.noise_word}"
    check("repair measures a faulty clip", repair_scan)

    def main_window() -> str:
        from PySide6.QtWidgets import QApplication
        from .context import AppContext
        from .ui.main_window import MainWindow
        application = QApplication.instance() or QApplication(sys.argv[:1])
        context = AppContext()
        try:
            window = MainWindow(context)
            window.show()
            application.processEvents()
            for key in window.panels:
                window.go_to(key)
                application.processEvents()
            return f"{len(window.panels)} screens built and opened"
        finally:
            context.shutdown()
    check("the main window builds", main_window)

    def playback_backend() -> str:
        # The preview player depends on a Qt plugin that a frozen build can silently
        # leave behind; with it missing, every preview stays blank and nothing says why.
        from PySide6.QtMultimedia import QMediaFormat
        formats = QMediaFormat().supportedFileFormats(QMediaFormat.ConversionMode.Decode)
        if not formats:
            raise RuntimeError("Qt Multimedia has no decoding backend")
        return f"{len(formats)} container formats can be previewed"
    check("the preview player has a backend", playback_backend)

    failed = [r for r in results if not r["ok"]]
    summary = {
        "app": APP_NAME, "version": __version__, "frozen": is_frozen(),
        "install_dir": str(install_dir()), "passed": len(results) - len(failed),
        "failed": len(failed), "checks": results,
    }
    text = json.dumps(summary, indent=2)
    if report_path is not None:
        report_path.write_text(text, encoding="utf-8")
    if sys.stdout is not None:
        for r in results:
            print(f"  {'OK  ' if r['ok'] else 'FAIL'}  {r['check']}  -- {r['detail']}")
        print(f"{summary['passed']} passed, {summary['failed']} failed")
    return len(failed)
