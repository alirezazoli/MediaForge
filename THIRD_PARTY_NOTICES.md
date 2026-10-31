# Third-party software in MediaForge Studio

The installer bundles the components below. Each keeps its own licence; the full texts
are in this folder or in the component's own folder inside the installation.

| Component | Licence | Where it is | Source |
|---|---|---|---|
| FFmpeg 9.0.2 (gyan.dev "full" build) | GNU GPL v3 | `ffmpeg\ffmpeg.exe`, `ffmpeg\ffprobe.exe`; licence in `licenses\ffmpeg\` | https://ffmpeg.org/download.html · build: https://www.gyan.dev/ffmpeg/builds/ |
| Qt 6 / PySide6 | GNU LGPL v3 | `_internal\PySide6\` (dynamically linked, replaceable) | https://code.qt.io · https://pypi.org/project/PySide6/ |
| FFmpeg libraries used by Qt Multimedia | GNU LGPL v2.1+ | `_internal\PySide6\` | https://ffmpeg.org |
| Python 3.14 | PSF License | `_internal\` | https://www.python.org |
| NumPy | BSD 3-Clause | `_internal\numpy\` | https://numpy.org |
| Pillow | MIT-CMU (HPND) | `_internal\PIL\` | https://python-pillow.org |
| Requests (and urllib3, idna, certifi, charset-normalizer) | Apache 2.0 / MIT / BSD / MPL 2.0 | `_internal\` | https://requests.readthedocs.io |

## FFmpeg

`ffmpeg.exe` and `ffprobe.exe` are unmodified binaries from the gyan.dev "full" build,
which is licensed under the GNU GPL version 3 because it includes GPL components (x264,
x265, libvidstab and others). They run as separate programs; MediaForge Studio calls them
on the command line and does not link to them. The corresponding source code is available
from https://ffmpeg.org and the build configuration from https://www.gyan.dev/ffmpeg/builds/.
You may replace them with any other FFmpeg build by overwriting the two files, or point
the app at a different one in Settings.

## Qt and PySide6

Qt and PySide6 are used under the LGPL v3. They are shipped as separate dynamic libraries
in `_internal\PySide6\`, which you may replace with your own builds of the same version.
The Qt source is available from https://code.qt.io.

## Engines downloaded on request

The AI engines offered in the Model Hub (Real-ESRGAN, RIFE, Whisper, Demucs and others)
are not part of the installer. Each is downloaded only when you ask for it, and the Hub
shows its licence before anything is downloaded.
