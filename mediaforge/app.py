"""Entry point.

    python -m mediaforge            launch the GUI
    python -m mediaforge --doctor   print an environment report and exit
"""

from __future__ import annotations

import sys


def doctor() -> int:
    from .config import Credentials, Settings, app_data_dir
    from .ffmpeg_tools import FfmpegLocator
    from .providers import build_registry

    settings = Settings.load()
    credentials = Credentials()
    locator = FfmpegLocator(settings.ffmpeg_path, settings.ffprobe_path)
    registry = build_registry(credentials, settings)

    print("MediaForge Studio -- environment report")
    print("=" * 60)
    print(f"Python        : {sys.version.split()[0]}")
    print(f"Config folder : {app_data_dir()}")
    print(f"Workspace     : {settings.workspace}")
    print(f"ffmpeg        : {locator.version() if locator.available else 'NOT FOUND'}")
    if not locator.available:
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


def main() -> int:
    if "--doctor" in sys.argv:
        return doctor()

    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("PySide6 is required. Install it with:\n\n    pip install PySide6\n")
        return 1

    from .context import AppContext
    from .ui.main_window import MainWindow

    application = QApplication(sys.argv)
    application.setApplicationName("MediaForge Studio")
    application.setOrganizationName("MediaForge")

    context = AppContext()
    window = MainWindow(context)
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
