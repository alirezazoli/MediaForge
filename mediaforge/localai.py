"""Local, free, offline AI tooling.

Everything here runs on the user's machine with no API key and no per-use cost. Two
install shapes are automated:

  ncnn-vulkan zips  self-contained .exe + models, any Vulkan GPU (AMD/Intel/NVIDIA)
  pip packages      installed into the running interpreter

Nothing downloads until the user explicitly asks for it in the Model Hub.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .catalog import load_local_tools
from .config import app_data_dir
from .providers.http import download

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

# ncnn progress lines are a bare percentage on their own line: "37.50%"
_PERCENT = re.compile(r"^([\d.]+)%$")


def tools_dir() -> Path:
    # Engines live with the machine, not with the profile: an isolated test run must
    # still find an already-installed Real-ESRGAN rather than reporting it missing and
    # re-downloading tens of megabytes.
    override = os.environ.get("MEDIAFORGE_TOOLS")
    directory = Path(override) if override else app_data_dir(respect_override=False) / "tools"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


@dataclass
class LocalTool:
    id: str
    name: str
    category: str
    media: list[str]
    license: str
    repo: str
    summary: str
    install: dict[str, Any]
    options: dict[str, dict]

    @property
    def install_kind(self) -> str:
        return self.install.get("kind", "manual")

    @property
    def size_mb(self) -> int:
        return int(self.install.get("size_mb", 0))

    @property
    def root(self) -> Path:
        return tools_dir() / self.id

    def executable_path(self) -> Path | None:
        """Finds the tool's .exe anywhere under its folder (zips nest a version dir)."""
        name = self.install.get("executable")
        if not name:
            return None
        direct = self.root / name
        if direct.exists():
            return direct
        return next(iter(sorted(self.root.rglob(name))), None)

    @property
    def installed(self) -> bool:
        if self.install_kind == "zip":
            return self.executable_path() is not None
        if self.install_kind == "pip":
            module = self.install.get("module", "")
            if not module:
                return False
            import importlib.util
            try:
                return importlib.util.find_spec(module) is not None
            except (ImportError, ValueError, ModuleNotFoundError):
                return False
        return False  # 'manual' tools are never auto-detected

    @property
    def status(self) -> str:
        if self.install_kind == "manual":
            return "manual install"
        return "installed" if self.installed else "not installed"


class LocalToolRegistry:
    def __init__(self) -> None:
        self.tools: list[LocalTool] = []
        self.reload()

    def reload(self) -> None:
        try:
            data = load_local_tools()
        except Exception as error:
            print(f"[localai] could not load localtools.json: {error}")
            self.tools = []
            return
        self.tools = [
            LocalTool(
                id=entry["id"], name=entry.get("name", entry["id"]),
                category=entry.get("category", "other"), media=entry.get("media", []),
                license=entry.get("license", ""), repo=entry.get("repo", ""),
                summary=entry.get("summary", ""), install=entry.get("install", {}),
                options=entry.get("options", {}),
            )
            for entry in data.get("tools", []) if entry.get("id")
        ]

    def get(self, tool_id: str) -> LocalTool | None:
        return next((tool for tool in self.tools if tool.id == tool_id), None)

    def by_category(self, category: str) -> list[LocalTool]:
        return [tool for tool in self.tools if tool.category == category]

    def installed_tools(self) -> list[LocalTool]:
        return [tool for tool in self.tools if tool.installed]


# ======================================================================================
# Installation
# ======================================================================================

def install_tool(
    tool: LocalTool,
    progress: Callable[[float, str], None] = lambda fraction, message: None,
) -> str:
    """Downloads/installs a tool. Raises RuntimeError with a readable message on failure."""
    if tool.install_kind == "zip":
        return _install_zip(tool, progress)
    if tool.install_kind == "pip":
        return _install_pip(tool, progress)
    raise RuntimeError(
        f"{tool.name} needs a manual install -- it is too large or too setup-heavy to "
        f"automate safely.\nFollow the instructions at {tool.repo}"
    )


def _install_zip(tool: LocalTool, progress) -> str:
    url = tool.install.get("url", "")
    if not url:
        raise RuntimeError(f"{tool.name} has no download URL in the catalogue.")
    tool.root.mkdir(parents=True, exist_ok=True)
    archive = tool.root / "download.zip"

    progress(0.05, f"Downloading {tool.name} (~{tool.size_mb} MB)")
    download(url, archive, timeout=1800)

    progress(0.75, "Extracting")
    try:
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(tool.root)
    except zipfile.BadZipFile as error:
        archive.unlink(missing_ok=True)
        raise RuntimeError(
            f"The download for {tool.name} was not a valid zip. The release URL may have "
            f"moved -- check {tool.repo}/releases"
        ) from error
    archive.unlink(missing_ok=True)

    executable = tool.executable_path()
    if executable is None:
        raise RuntimeError(
            f"Extracted {tool.name} but could not find "
            f"'{tool.install.get('executable')}' inside it."
        )
    progress(1.0, f"Installed to {executable.parent}")
    return str(executable)


def _install_pip(tool: LocalTool, progress) -> str:
    package = tool.install.get("package", "")
    if not package:
        raise RuntimeError(f"{tool.name} has no pip package in the catalogue.")
    progress(0.1, f"pip install {package} (~{tool.size_mb} MB)")
    process = subprocess.run(
        [sys.executable, "-m", "pip", "install", package],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=_NO_WINDOW,
    )
    if process.returncode != 0:
        tail = (process.stderr or process.stdout).strip().splitlines()[-15:]
        raise RuntimeError(f"pip install failed:\n" + "\n".join(tail))
    progress(1.0, "Installed")
    return package


# ======================================================================================
# Runners -- one per tool family
# ======================================================================================

def _run(argv: list[str], progress, label: str, cancel: Callable[[], bool] | None = None,
         expected_items: int = 0) -> str:
    """Runs a console tool, streaming its stderr (where these tools report progress)."""
    process = subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", creationflags=_NO_WINDOW,
    )
    lines: list[str] = []
    # The ncnn tools print a bare percentage, but it is PER IMAGE and restarts at 0.00%
    # for every frame -- reading it as overall progress makes the bar saw back and forth
    # once per frame. Each reset means one more image finished, so counting the resets is
    # what actually advances, and (finished + current) can only ever go up.
    finished = 0
    last_percent = -1.0
    assert process.stdout is not None
    for line in process.stdout:
        if cancel and cancel():
            process.terminate()
            raise RuntimeError("Cancelled")
        stripped = line.strip()
        if not stripped:
            continue
        lines.append(stripped)
        match = _PERCENT.match(stripped)
        if match:
            percent = float(match.group(1))
            if percent < last_percent - 0.001:
                finished += 1
            last_percent = percent
            if expected_items > 1:
                fraction = (finished + percent / 100.0) / expected_items
                progress(min(1.0, fraction), f"{label}: frame {finished + 1} of {expected_items}")
            else:
                progress(min(1.0, percent / 100.0), f"{label} {stripped}")
        elif "%" in stripped:
            progress(0.5, f"{label}: {stripped[:60]}")
    process.wait()
    if process.returncode != 0:
        raise RuntimeError(f"{label} failed (exit {process.returncode}):\n" +
                           "\n".join(lines[-15:]))
    return "\n".join(lines[-5:])


def upscale_image(
    tool: LocalTool, source: Path, destination: Path, options: dict,
    progress=lambda f, m: None, cancel=None,
) -> Path:
    """Real-ESRGAN / Real-CUGAN / waifu2x on a single still."""
    executable = tool.executable_path()
    if executable is None:
        raise RuntimeError(f"{tool.name} is not installed. Install it in the Model Hub.")
    destination.parent.mkdir(parents=True, exist_ok=True)

    argv = ([str(executable), "-i", str(source), "-o", str(destination)]
            + _upscaler_flags(tool, options))

    progress(0.2, f"{tool.name} upscaling")
    _run(argv, progress, tool.name, cancel)
    if not destination.exists():
        raise RuntimeError(f"{tool.name} produced no output file.")
    progress(1.0, "Done")
    return destination


def _upscaler_flags(tool: LocalTool, options: dict) -> list[str]:
    """Scale, model and GPU, shared by the still and the video path.

    The GPU flag is the important one. ncnn's own device default picked the Intel iGPU on
    a laptop with an RTX in it -- measured at 4.3s versus 1.0s on the same frame.
    """
    from . import gpu

    flags: list[str] = []
    if options.get("scale"):
        flags += ["-s", str(options["scale"])]
    if options.get("model") and tool.id == "realesrgan":
        flags += ["-n", str(options["model"])]
    if options.get("noise") not in (None, "") and tool.id in ("realcugan", "waifu2x"):
        flags += ["-n", str(options["noise"])]
    if options.get("tile"):
        flags += ["-t", str(options["tile"])]

    device_index = options.get("gpu")
    if device_index is None:
        chosen = gpu.best_device(tool)
        device_index = chosen.index if chosen else None
    if device_index is not None and int(device_index) >= 0:
        flags += ["-g", str(int(device_index))]
    return flags


# A PNG frame is roughly width*height*3 bytes before compression, and an upscale keeps
# both the input and the output batch on disk at once. Two gigabytes of scratch is a
# tolerable ceiling on a laptop.
CHUNK_BUDGET_BYTES = 2_000_000_000


def _chunk_seconds(info, scale: int) -> float:
    """How much video to hold on disk at a time, in seconds.

    Extracting a whole video to PNG first is what the naive version did, and a 60-second
    1080p clip needs over 5 GB before a single frame is upscaled -- then several times
    that again for the x4 output. Working in chunks bounds it regardless of length.

    The budget is computed in FRAMES and converted back, because a floor expressed in
    seconds silently breaks the guarantee: two seconds of 4K at x4 is 50 GB, so clamping
    the answer up to "at least 2s" would hand back a chunk 25 times over budget.
    """
    fps = info.fps or 30.0
    per_input = info.width * info.height * 3
    per_frame = per_input + per_input * scale * scale
    if per_frame <= 0 or fps <= 0:
        return 20.0
    # At least one frame -- below that there is nothing left to give up.
    frames = max(1, int(CHUNK_BUDGET_BYTES // per_frame))
    return min(60.0, frames / fps)


def upscale_video(
    tool: LocalTool, source: Path, destination: Path, options: dict, locator,
    progress=lambda f, m: None, cancel=None,
) -> Path:
    """Frames out -> upscale every frame -> frames back in, keeping the original audio.

    ncnn upscalers only speak directories of stills, so video means a round trip through
    PNG. It is done a chunk at a time: the source is split on keyframes with a stream
    copy, each piece is expanded, upscaled and re-encoded on its own, and the pieces are
    concatenated at the end. Peak disk stays bounded no matter how long the video is, and
    a failure halfway through has not already written 40 GB of frames.
    """
    from . import ffmpeg_tools

    executable = tool.executable_path()
    if executable is None:
        raise RuntimeError(f"{tool.name} is not installed. Install it in the Model Hub.")
    locator.require()

    info = ffmpeg_tools.probe(locator, source)
    fps = info.fps or 30.0
    scale = int(options.get("scale") or 4)
    work = destination.parent / f".{destination.stem}_work"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    pieces_dir = work / "pieces"
    frames_in = work / "in"
    frames_out = work / "out"
    encoded_dir = work / "encoded"
    for directory in (pieces_dir, frames_in, frames_out, encoded_dir):
        directory.mkdir(parents=True, exist_ok=True)

    flags = _upscaler_flags(tool, options)

    try:
        # -- split ------------------------------------------------------------------
        chunk = _chunk_seconds(info, scale)
        progress(0.02, f"Splitting into {chunk:.0f}s pieces")
        split = subprocess.run(
            [locator.ffmpeg, "-y", "-v", "error", "-i", str(source), "-an",
             "-c:v", "copy", "-f", "segment", "-segment_time", f"{chunk:.3f}",
             "-reset_timestamps", "1", str(pieces_dir / "piece_%05d.mkv")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=_NO_WINDOW,
        )
        pieces = sorted(pieces_dir.glob("piece_*.mkv"))
        if split.returncode != 0 or not pieces:
            raise RuntimeError(f"Could not split the video:\n{(split.stderr or '')[-700:]}")

        total_frames = max(1, int(round(info.duration * fps)))
        done_frames = 0
        encoded: list[Path] = []

        for index, piece in enumerate(pieces):
            if cancel and cancel():
                raise RuntimeError("Cancelled")
            for directory in (frames_in, frames_out):
                shutil.rmtree(directory, ignore_errors=True)
                directory.mkdir(parents=True, exist_ok=True)

            share = done_frames / total_frames
            progress(0.02 + 0.94 * share,
                     f"Piece {index + 1} of {len(pieces)}: extracting frames")
            extract = subprocess.run(
                [locator.ffmpeg, "-y", "-v", "error", "-i", str(piece),
                 str(frames_in / "%08d.png")],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                creationflags=_NO_WINDOW,
            )
            count = len(list(frames_in.glob("*.png")))
            if extract.returncode != 0 or not count:
                raise RuntimeError(
                    f"Frame extraction failed on piece {index + 1}:"
                    f"\n{(extract.stderr or '')[-700:]}"
                )

            def report(fraction: float, message: str, _index=index, _count=count,
                       _done=done_frames) -> None:
                overall = (_done + fraction * _count) / total_frames
                progress(0.02 + 0.94 * overall,
                         f"Piece {_index + 1}/{len(pieces)}: {message}")

            argv = [str(executable), "-i", str(frames_in), "-o", str(frames_out),
                    "-f", "png"] + flags
            _run(argv, report, tool.name, cancel, expected_items=count)

            produced = len(list(frames_out.glob("*.png")))
            if produced < count:
                raise RuntimeError(
                    f"{tool.name} upscaled only {produced} of {count} frames in piece "
                    f"{index + 1}. This is usually the GPU running out of memory — try a "
                    f"smaller scale, or set a tile size."
                )

            target = encoded_dir / f"piece_{index:05d}.mkv"
            progress(0.02 + 0.94 * ((done_frames + count) / total_frames),
                     f"Piece {index + 1} of {len(pieces)}: encoding")
            encode = subprocess.run(
                [locator.ffmpeg, "-y", "-v", "error", "-framerate", f"{fps:.6f}",
                 "-i", str(frames_out / "%08d.png"), "-c:v", "libx264", "-crf", "16",
                 "-preset", "medium", "-pix_fmt", "yuv420p", str(target)],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                creationflags=_NO_WINDOW,
            )
            if encode.returncode != 0 or not target.exists():
                raise RuntimeError(f"Re-encode failed on piece {index + 1}:"
                                   f"\n{(encode.stderr or '')[-700:]}")
            encoded.append(target)
            done_frames += count

        # -- join, and put the original audio back ----------------------------------
        progress(0.97, "Joining the pieces")
        listing = work / "pieces.txt"
        listing.write_text(
            "\n".join(f"file '{path.as_posix()}'" for path in encoded), encoding="utf-8"
        )
        argv = [locator.ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "0",
                "-i", str(listing)]
        if info.has_audio:
            argv += ["-i", str(source), "-map", "0:v", "-map", "1:a", "-c:a", "copy",
                     "-shortest"]
        argv += ["-c:v", "copy", str(destination)]
        join = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", creationflags=_NO_WINDOW)
        if join.returncode != 0 or not destination.exists():
            raise RuntimeError(f"Joining the pieces failed:\n{(join.stderr or '')[-700:]}")

        progress(1.0, "Done")
        return destination
    finally:
        shutil.rmtree(work, ignore_errors=True)


def interpolate_video(
    tool: LocalTool, source: Path, destination: Path, options: dict, locator,
    progress=lambda f, m: None, cancel=None,
) -> Path:
    """RIFE frame interpolation: higher fps, or smooth slow motion."""
    from . import ffmpeg_tools

    executable = tool.executable_path()
    if executable is None:
        raise RuntimeError("RIFE is not installed. Install it in the Model Hub.")
    locator.require()

    work = destination.parent / f".{destination.stem}_rife"
    frames_in, frames_out = work / "in", work / "out"
    for directory in (frames_in, frames_out):
        if directory.exists():
            shutil.rmtree(directory, ignore_errors=True)
        directory.mkdir(parents=True, exist_ok=True)

    try:
        info = ffmpeg_tools.probe(locator, source)
        source_fps = info.fps or 30.0
        factor = int(options.get("factor", 2))

        progress(0.05, "Extracting frames")
        subprocess.run(
            [locator.ffmpeg, "-y", "-v", "error", "-i", str(source),
             str(frames_in / "%08d.png")],
            check=True, capture_output=True, creationflags=_NO_WINDOW,
        )
        count = len(list(frames_in.glob("*.png")))
        if count < 2:
            raise RuntimeError("Interpolation needs at least two frames.")

        progress(0.2, f"Interpolating {count} -> {count * factor} frames")
        argv = [str(executable), "-i", str(frames_in), "-o", str(frames_out),
                "-n", str(count * factor), "-f", "png"]
        if options.get("model"):
            model_dir = executable.parent / str(options["model"])
            if model_dir.is_dir():
                argv += ["-m", str(model_dir)]
        _run(argv, lambda f, m: progress(0.2 + 0.65 * f, m), "RIFE", cancel)

        target_fps = source_fps * factor
        progress(0.9, f"Re-encoding at {target_fps:.2f} fps")
        argv = [locator.ffmpeg, "-y", "-v", "error", "-framerate", f"{target_fps:.6f}",
                "-i", str(frames_out / "%08d.png")]
        if info.has_audio:
            argv += ["-i", str(source), "-map", "0:v", "-map", "1:a", "-c:a", "copy"]
        argv += ["-c:v", "libx264", "-crf", "17", "-preset", "medium",
                 "-pix_fmt", "yuv420p", str(destination)]
        encode = subprocess.run(argv, capture_output=True, text=True, creationflags=_NO_WINDOW)
        if encode.returncode != 0:
            raise RuntimeError(f"Re-encode failed:\n{encode.stderr[-800:]}")

        progress(1.0, "Done")
        return destination
    finally:
        shutil.rmtree(work, ignore_errors=True)


def remove_background(
    tool: LocalTool, source: Path, destination: Path, options: dict,
    progress=lambda f, m: None, cancel=None,
) -> Path:
    """rembg on a still, producing a transparent PNG."""
    progress(0.2, "Removing background")
    argv = [sys.executable, "-m", "rembg", "i"]
    if options.get("model"):
        argv += ["-m", str(options["model"])]
    if options.get("alpha_matting"):
        argv += ["-a"]
    argv += [str(source), str(destination)]

    process = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                             errors="replace", creationflags=_NO_WINDOW)
    if process.returncode != 0 or not destination.exists():
        tail = (process.stderr or process.stdout).strip().splitlines()[-12:]
        raise RuntimeError("rembg failed:\n" + "\n".join(tail))
    progress(1.0, "Done")
    return destination


def transcribe(
    tool: LocalTool, source: Path, options: dict,
    progress=lambda f, m: None, cancel=None,
) -> list[dict]:
    """faster-whisper -> [{'start', 'end', 'text'}] ready for write_srt."""
    try:
        from faster_whisper import WhisperModel
    except ImportError as error:
        raise RuntimeError(
            "faster-whisper is not installed. Install it in the Model Hub."
        ) from error

    model_size = options.get("model", "base")
    device = options.get("device", "auto")
    if device == "auto":
        device = "cpu"  # CUDA needs matching cuDNN; CPU always works, int8 keeps it usable
    compute_type = "int8" if device == "cpu" else "float16"

    progress(0.1, f"Loading Whisper '{model_size}' on {device}")
    model = WhisperModel(model_size, device=device, compute_type=compute_type)

    progress(0.25, "Transcribing")
    language = options.get("language") or None
    segments, info = model.transcribe(str(source), language=language, vad_filter=True,
                                      word_timestamps=True)

    collected: list[dict] = []
    total = getattr(info, "duration", 0.0) or 0.0
    for segment in segments:
        if cancel and cancel():
            raise RuntimeError("Cancelled")
        collected.append({
            "start": float(segment.start), "end": float(segment.end),
            "text": segment.text.strip(),
        })
        if total:
            progress(min(0.95, 0.25 + 0.7 * segment.end / total),
                     f"{segment.end:.0f}s / {total:.0f}s")
    progress(1.0, f"{len(collected)} segments")
    return collected


def separate_audio(
    tool: LocalTool, source: Path, output_dir: Path, options: dict,
    progress=lambda f, m: None, cancel=None,
) -> list[Path]:
    """Demucs stem separation."""
    output_dir.mkdir(parents=True, exist_ok=True)
    argv = [sys.executable, "-m", "demucs", "-o", str(output_dir),
            "-n", str(options.get("model", "htdemucs"))]
    stem = options.get("stem", "all")
    if stem and stem != "all":
        argv += ["--two-stems", str(stem)]
    argv += [str(source)]

    progress(0.15, "Separating stems (this is slow on CPU)")
    _run(argv, progress, "Demucs", cancel)
    produced = sorted(output_dir.rglob("*.wav"))
    if not produced:
        raise RuntimeError("Demucs produced no stems.")
    progress(1.0, f"{len(produced)} stems")
    return produced


def speak(
    tool: LocalTool, text: str, destination: Path, options: dict,
    progress=lambda f, m: None, cancel=None,
) -> Path:
    """Kokoro TTS -- free local voiceover."""
    try:
        import soundfile
        from kokoro import KPipeline
    except ImportError as error:
        raise RuntimeError(
            "Kokoro TTS needs `kokoro` and `soundfile`. Install Kokoro in the Model Hub, "
            "then run: pip install soundfile"
        ) from error

    progress(0.2, "Loading Kokoro")
    voice = str(options.get("voice", "af_heart"))
    pipeline = KPipeline(lang_code=voice[0] if voice else "a")

    progress(0.4, "Synthesising")
    chunks = []
    for _, _, audio in pipeline(text, voice=voice, speed=float(options.get("speed", 1.0))):
        if cancel and cancel():
            raise RuntimeError("Cancelled")
        chunks.append(audio)
    if not chunks:
        raise RuntimeError("Kokoro produced no audio.")

    import numpy
    destination.parent.mkdir(parents=True, exist_ok=True)
    soundfile.write(str(destination), numpy.concatenate(chunks), 24000)
    progress(1.0, "Done")
    return destination
