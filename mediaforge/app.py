"""Entry point.

    MediaForge.exe / python -m mediaforge     launch the GUI
    --doctor                                  print an environment report and exit
    --selftest [--report PATH]                run the end-to-end checks, exit code = failures
    --version                                 print the version and exit
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

if not __package__:
    # Started as a file -- `python mediaforge/app.py`, or Run in an editor -- where relative
    # imports fail before anything is shown. Run the package the way `-m` would instead.
    import runpy

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    runpy.run_module("mediaforge", run_name="__main__", alter_sys=True)
    raise SystemExit(0)

from .paths import APP_NAME, __version__

log = logging.getLogger("mediaforge")


def doctor() -> int:
    from .config import Credentials, Settings, app_data_dir
    from .ffmpeg_tools import FfmpegLocator
    from .providers import build_registry
    from .runtime import log_file

    settings = Settings.load()
    credentials = Credentials()
    locator = FfmpegLocator(settings.ffmpeg_path, settings.ffprobe_path)
    registry = build_registry(credentials, settings)

    print(f"{APP_NAME} {__version__} -- environment report")
    print("=" * 60)
    print(f"Python        : {sys.version.split()[0]}")
    print(f"Config folder : {app_data_dir()}")
    print(f"Log file      : {log_file()}")
    print(f"Workspace     : {settings.workspace}")
    print(f"ffmpeg        : {locator.version() if locator.available else 'NOT FOUND'}")
    if locator.available:
        print(f"                {locator.ffmpeg}")
    else:
        print("                Install with: winget install Gyan.FFmpeg")

    try:
        from PySide6 import __version__ as qt_version
        print(f"PySide6       : {qt_version}")
    except ImportError:
        print("PySide6       : NOT INSTALLED -- run: pip install PySide6")

    print()
    print("Providers")
    print("-" * 60)
    for provider in registry:
        marker = "[ready]" if provider.configured else "[no key]"
        source = (credentials.source_of(provider.credential_slot)
                  if provider.credential_slot else "none needed")
        print(f"{marker:9} {provider.name}")
        print(f"          key: {source}")
        print(f"          models: {len(provider.models)}   "
              f"capabilities: {', '.join(sorted(c.value for c in provider.capabilities()))}")
    return 0


def _option_value(flag: str) -> str | None:
    if flag in sys.argv:
        index = sys.argv.index(flag)
        if index + 1 < len(sys.argv):
            return sys.argv[index + 1]
    return None


def main() -> int:
    if any(flag in sys.argv for flag in ("--version", "--doctor", "--selftest")):
        from .runtime import prepare_console
        prepare_console()
    if "--version" in sys.argv:
        print(f"{APP_NAME} {__version__}")
        return 0
    if "--doctor" in sys.argv:
        return doctor()
    if "--selftest" in sys.argv:
        from . import selftest
        report = _option_value("--report")
        return selftest.run(Path(report) if report else None)

    try:
        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("PySide6 is required. Install it with:\n\n    pip install PySide6\n")
        return 1

    from . import runtime
    from .paths import icon_path

    runtime.configure_logging()
    runtime.install_crash_hooks()
    runtime.set_windows_app_id()

    application = QApplication(sys.argv)
    application.setApplicationName(APP_NAME)
    application.setApplicationVersion(__version__)
    application.setOrganizationName("MediaForge")
    icon = icon_path()
    if icon.exists():
        application.setWindowIcon(QIcon(str(icon)))

    instance = runtime.SingleInstance()
    if not instance.is_primary:
        log.info("Another instance is running; handed over to it.")
        return 0

    from .context import AppContext
    from .ui.main_window import MainWindow

    context = AppContext()
    window = MainWindow(context)

    def bring_to_front() -> None:
        if window.isMinimized():
            window.showNormal()
        window.show()
        window.raise_()
        window.activateWindow()

    instance.on_activate = bring_to_front
    window.show_fitted()
    exit_code = application.exec()
    log.info("Exited with code %s", exit_code)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
