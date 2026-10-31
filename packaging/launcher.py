"""PyInstaller entry point. The real entry is mediaforge.app.main; this file exists
because PyInstaller wants a script, and `-m mediaforge` is not one."""

from mediaforge.app import main

if __name__ == "__main__":
    raise SystemExit(main())
