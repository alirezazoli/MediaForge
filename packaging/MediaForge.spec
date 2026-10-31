# PyInstaller spec for MediaForge Studio. Built by packaging/build.ps1:
#   python -m PyInstaller packaging/MediaForge.spec --workpath <dir> --distpath <dir>
#
# One folder, not one file: a one-file build unpacks ~300 MB of Qt into a temp folder on
# every launch, which is slow and trips antivirus heuristics. The installer ships the
# folder; ffmpeg and the licences are copied in next to MediaForge.exe by build.ps1.

import re
from pathlib import Path

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct,
    VSVersionInfo,
)

ROOT = Path(SPECPATH).parent
PACKAGE = ROOT / "mediaforge"
VERSION = re.search(r'__version__ = "([^"]+)"',
                    (PACKAGE / "paths.py").read_text(encoding="utf-8")).group(1)
NUMBERS = tuple(int(part) for part in (VERSION.split(".") + ["0"] * 4)[:4])

version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=NUMBERS, prodvers=NUMBERS),
    kids=[
        StringFileInfo([StringTable("040904B0", [
            StringStruct("CompanyName", "MediaForge"),
            StringStruct("FileDescription", "MediaForge Studio"),
            StringStruct("FileVersion", VERSION),
            StringStruct("InternalName", "MediaForge"),
            StringStruct("OriginalFilename", "MediaForge.exe"),
            StringStruct("ProductName", "MediaForge Studio"),
            StringStruct("ProductVersion", VERSION),
        ])]),
        VarFileInfo([VarStruct("Translation", [1033, 1200])]),
    ],
)

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    datas=[
        (str(PACKAGE / "catalog" / "*.json"), "mediaforge/catalog"),
        (str(PACKAGE / "assets" / "*"), "mediaforge/assets"),
    ],
    # Imported inside functions, so the import scan cannot see them.
    hiddenimports=[
        "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtNetwork",
        "numpy", "PIL.Image", "PIL.ImageDraw", "PIL.ImageFilter",
    ],
    # Installed in the build environment but never used by the app.
    excludes=[
        "tkinter", "unittest", "pydoc_data", "IPython", "matplotlib", "scipy", "pandas",
        "django", "flask", "pytest", "PyInstaller",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MediaForge",
    icon=str(PACKAGE / "assets" / "mediaforge.ico"),
    version=version_info,
    console=False,
    upx=False,
)

coll = COLLECT(exe, a.binaries, a.datas, name="MediaForge", upx=False)
