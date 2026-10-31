"""The rest of the non-AI toolbox.

Kept separate from ffmpeg_tools so the core stays readable. Same contract: every function
builds an FfmpegCommand and executes nothing.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from .ffmpeg_tools import FfmpegCommand, FfmpegLocator, _NO_WINDOW

# ======================================================================================
# Export presets -- platform-correct delivery in one click
# ======================================================================================

EXPORT_PRESETS: dict[str, dict] = {
    "YouTube 1080p": {"width": 1920, "height": 1080, "fps": 30, "crf": 18, "codec": "libx264",
                      "audio_bitrate": "384k", "container": ".mp4"},
    "YouTube 4K": {"width": 3840, "height": 2160, "fps": 30, "crf": 18, "codec": "libx264",
                   "audio_bitrate": "384k", "container": ".mp4"},
    "YouTube Shorts / TikTok / Reels": {"width": 1080, "height": 1920, "fps": 30, "crf": 20,
                                        "codec": "libx264", "audio_bitrate": "192k",
                                        "container": ".mp4"},
    "Instagram feed 1:1": {"width": 1080, "height": 1080, "fps": 30, "crf": 20,
                           "codec": "libx264", "audio_bitrate": "192k", "container": ".mp4"},
    "Instagram portrait 4:5": {"width": 1080, "height": 1350, "fps": 30, "crf": 20,
                               "codec": "libx264", "audio_bitrate": "192k", "container": ".mp4"},
    "X / Twitter": {"width": 1280, "height": 720, "fps": 30, "crf": 23, "codec": "libx264",
                    "audio_bitrate": "128k", "container": ".mp4"},
    "LinkedIn": {"width": 1920, "height": 1080, "fps": 30, "crf": 21, "codec": "libx264",
                 "audio_bitrate": "192k", "container": ".mp4"},
    "WhatsApp / small file": {"width": 854, "height": 480, "fps": 30, "crf": 28,
                              "codec": "libx264", "audio_bitrate": "96k", "container": ".mp4"},
    "Web VP9 (small, modern)": {"width": 1920, "height": 1080, "fps": 30, "crf": 32,
                                "codec": "libvpx-vp9", "audio_bitrate": "128k",
                                "container": ".webm"},
    "HEVC 4K (small 4K)": {"width": 3840, "height": 2160, "fps": 30, "crf": 24,
                           "codec": "libx265", "audio_bitrate": "256k", "container": ".mp4"},
    "ProRes 422 (editing master)": {"width": 0, "height": 0, "fps": 0, "crf": 0,
                                    "codec": "prores_ks", "audio_bitrate": "512k",
                                    "container": ".mov"},
    "Lossless archive": {"width": 0, "height": 0, "fps": 0, "crf": 0, "codec": "libx264",
                         "audio_bitrate": "512k", "container": ".mkv", "lossless": True},
}


def export_with_preset(
    locator: FfmpegLocator, source: Path, output: Path, preset_name: str,
    duration_hint: float = 0.0, hardware: str = "none",
) -> FfmpegCommand:
    preset = EXPORT_PRESETS.get(preset_name)
    if preset is None:
        raise ValueError(f"Unknown export preset '{preset_name}'")

    argv = [locator.ffmpeg, "-y", "-hide_banner", "-i", str(source)]
    filters: list[str] = []
    width, height = preset["width"], preset["height"]
    if width and height:
        filters.append(
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"
        )
    if preset["fps"]:
        filters.append(f"fps={preset['fps']}")
    if filters:
        argv += ["-vf", ",".join(filters)]

    codec = preset["codec"]
    if hardware == "nvidia" and codec in ("libx264", "libx265"):
        codec = "h264_nvenc" if codec == "libx264" else "hevc_nvenc"
        argv += ["-c:v", codec, "-preset", "p5", "-cq", str(preset["crf"] or 20)]
    elif hardware == "amd" and codec in ("libx264", "libx265"):
        codec = "h264_amf" if codec == "libx264" else "hevc_amf"
        argv += ["-c:v", codec, "-quality", "balanced"]
    elif hardware == "intel" and codec in ("libx264", "libx265"):
        codec = "h264_qsv" if codec == "libx264" else "hevc_qsv"
        argv += ["-c:v", codec, "-global_quality", str(preset["crf"] or 20)]
    elif codec == "prores_ks":
        argv += ["-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le"]
    elif preset.get("lossless"):
        argv += ["-c:v", "libx264", "-preset", "veryslow", "-qp", "0"]
    else:
        argv += ["-c:v", codec, "-crf", str(preset["crf"]), "-preset", "medium"]

    if codec != "prores_ks":
        argv += ["-pix_fmt", "yuv420p"]
    audio_codec = "libopus" if preset["container"] == ".webm" else "aac"
    argv += ["-c:a", audio_codec, "-b:a", preset["audio_bitrate"]]
    if preset["container"] == ".mp4":
        argv += ["-movflags", "+faststart"]   # lets web players start before full download
    argv += [str(output)]

    return FfmpegCommand(argv, output, duration_hint, f"Export: {preset_name}")


def available_encoders(locator: FfmpegLocator) -> dict[str, bool]:
    """Probes what this ffmpeg build can actually do.

    Encoders and filters are separate lists -- vidstab and drawtext are filters, so
    querying `-encoders` for them always says no. Both lists get read.
    """
    if not locator.available:
        return {}

    def query(flag: str) -> str:
        result = subprocess.run([locator.ffmpeg, "-hide_banner", flag],
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace", creationflags=_NO_WINDOW)
        return result.stdout or ""

    encoders = query("-encoders")
    filters = query("-filters")
    return {
        "nvidia": "h264_nvenc" in encoders,
        "amd": "h264_amf" in encoders,
        "intel": "h264_qsv" in encoders,
        "hevc": "libx265" in encoders,
        "vp9": "libvpx-vp9" in encoders,
        "av1": "libsvtav1" in encoders or "libaom-av1" in encoders,
        "prores": "prores_ks" in encoders,
        "vidstab": "vidstabdetect" in filters and "vidstabtransform" in filters,
        "drawtext": "drawtext" in filters,
        "subtitles": "subtitles" in filters,
        "nlmeans": "nlmeans" in filters,
        "bm3d": "bm3d" in filters,
        "zscale": "zscale" in filters,
        "deband": "deband" in filters,
    }


def _base(locator: FfmpegLocator) -> list[str]:
    locator.require()
    return [locator.ffmpeg, "-y", "-hide_banner"]


# ======================================================================================
# Cleanup: denoise, sharpen, deband, deblock, deinterlace
# ======================================================================================

DENOISE_METHODS = {
    "hqdn3d (fast, general)": "hqdn3d={luma_spatial}:{chroma_spatial}:{luma_tmp}:{chroma_tmp}",
    "nlmeans (slow, best quality)": "nlmeans=s={strength}",
    "atadenoise (temporal, low grain)": "atadenoise",
    "removegrain (film grain)": "removegrain=1",
    "bm3d (very slow, cleanest)": "bm3d=sigma={strength}:block=4:bstep=2:group=1:estim=basic",
}


def denoise_video(
    locator: FfmpegLocator, source: Path, output: Path, method: str = "hqdn3d (fast, general)",
    strength: float = 3.0, duration_hint: float = 0.0,
) -> FfmpegCommand:
    template = DENOISE_METHODS.get(method, DENOISE_METHODS["hqdn3d (fast, general)"])
    chain = template.format(
        strength=strength,
        luma_spatial=strength, chroma_spatial=strength * 0.75,
        luma_tmp=strength * 2, chroma_tmp=strength * 1.5,
    )
    argv = _base(locator) + [
        "-i", str(source), "-vf", chain,
        "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-c:a", "copy", str(output),
    ]
    return FfmpegCommand(argv, output, duration_hint, f"Denoise ({method.split(' ')[0]})")


def sharpen(
    locator: FfmpegLocator, source: Path, output: Path, amount: float = 1.0,
    radius: int = 5, duration_hint: float = 0.0,
) -> FfmpegCommand:
    radius = radius if radius % 2 else radius + 1   # unsharp requires odd matrix sizes
    argv = _base(locator) + [
        "-i", str(source), "-vf", f"unsharp={radius}:{radius}:{amount}:{radius}:{radius}:{amount * 0.5}",
        "-c:v", "libx264", "-crf", "18", "-c:a", "copy", str(output),
    ]
    return FfmpegCommand(argv, output, duration_hint, "Sharpen")


def deband(
    locator: FfmpegLocator, source: Path, output: Path, strength: float = 0.02,
    duration_hint: float = 0.0,
) -> FfmpegCommand:
    """Kills the banding that gradients pick up after heavy compression."""
    argv = _base(locator) + [
        "-i", str(source),
        "-vf", f"deband=1thr={strength}:2thr={strength}:3thr={strength}:4thr={strength}:range=16",
        "-c:v", "libx264", "-crf", "18", "-c:a", "copy", str(output),
    ]
    return FfmpegCommand(argv, output, duration_hint, "Deband")


def deinterlace(
    locator: FfmpegLocator, source: Path, output: Path, double_rate: bool = False,
    duration_hint: float = 0.0,
) -> FfmpegCommand:
    mode = "bwdif=mode=send_field" if double_rate else "bwdif=mode=send_frame"
    argv = _base(locator) + [
        "-i", str(source), "-vf", mode, "-c:v", "libx264", "-crf", "18",
        "-c:a", "copy", str(output),
    ]
    return FfmpegCommand(argv, output, duration_hint, "Deinterlace")


# ======================================================================================
# Two-pass vidstab -- proper stabilisation
# ======================================================================================

def stabilise_analyse(
    locator: FfmpegLocator, source: Path, transforms: Path, shakiness: int = 5,
    accuracy: int = 15, duration_hint: float = 0.0,
) -> FfmpegCommand:
    transforms.parent.mkdir(parents=True, exist_ok=True)
    escaped = str(transforms).replace("\\", "/").replace(":", "\\:")
    argv = _base(locator) + [
        "-i", str(source),
        "-vf", f"vidstabdetect=shakiness={shakiness}:accuracy={accuracy}:result='{escaped}'",
        "-f", "null", "-" if sys.platform != "win32" else "NUL",
    ]
    return FfmpegCommand(argv, transforms, duration_hint, "Stabilise: analyse")


def stabilise_apply(
    locator: FfmpegLocator, source: Path, transforms: Path, output: Path,
    smoothing: int = 30, zoom: float = 0.0, duration_hint: float = 0.0,
) -> FfmpegCommand:
    escaped = str(transforms).replace("\\", "/").replace(":", "\\:")
    argv = _base(locator) + [
        "-i", str(source),
        "-vf", f"vidstabtransform=input='{escaped}':smoothing={smoothing}:zoom={zoom}:"
               f"optzoom=1:interpol=bicubic,unsharp=5:5:0.8:3:3:0.4",
        "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-c:a", "copy", str(output),
    ]
    return FfmpegCommand(argv, output, duration_hint, "Stabilise: apply")


# ======================================================================================
# Geometry
# ======================================================================================

def crop(
    locator: FfmpegLocator, source: Path, output: Path, width: int, height: int,
    x: int = 0, y: int = 0, duration_hint: float = 0.0,
) -> FfmpegCommand:
    argv = _base(locator) + [
        "-i", str(source), "-vf", f"crop={width}:{height}:{x}:{y}",
        "-c:v", "libx264", "-crf", "18", "-c:a", "copy", str(output),
    ]
    return FfmpegCommand(argv, output, duration_hint, "Crop")


def rotate_flip(
    locator: FfmpegLocator, source: Path, output: Path, rotation: str = "90 clockwise",
    flip: str = "none", duration_hint: float = 0.0,
) -> FfmpegCommand:
    chain: list[str] = []
    rotations = {
        "90 clockwise": "transpose=1", "90 counter-clockwise": "transpose=2",
        "180": "transpose=1,transpose=1", "none": "",
    }
    if rotations.get(rotation):
        chain.append(rotations[rotation])
    flips = {"horizontal": "hflip", "vertical": "vflip", "none": ""}
    if flips.get(flip):
        chain.append(flips[flip])
    if not chain:
        chain.append("null")
    argv = _base(locator) + [
        "-i", str(source), "-vf", ",".join(chain),
        "-c:v", "libx264", "-crf", "18", "-c:a", "copy", str(output),
    ]
    return FfmpegCommand(argv, output, duration_hint, "Rotate / flip")


def fade(
    locator: FfmpegLocator, source: Path, output: Path, duration: float,
    fade_in: float = 0.5, fade_out: float = 0.5, audio_too: bool = True,
) -> FfmpegCommand:
    out_start = max(0.0, duration - fade_out)
    video_chain = f"fade=t=in:st=0:d={fade_in},fade=t=out:st={out_start:.3f}:d={fade_out}"
    argv = _base(locator) + ["-i", str(source), "-vf", video_chain]
    if audio_too:
        argv += ["-af", f"afade=t=in:st=0:d={fade_in},"
                        f"afade=t=out:st={out_start:.3f}:d={fade_out}"]
    else:
        argv += ["-c:a", "copy"]
    argv += ["-c:v", "libx264", "-crf", "18", str(output)]
    return FfmpegCommand(argv, output, duration, "Fade in / out")


def reverse(
    locator: FfmpegLocator, source: Path, output: Path, with_audio: bool = True,
    duration_hint: float = 0.0,
) -> FfmpegCommand:
    if with_audio:
        argv = _base(locator) + [
            "-i", str(source), "-vf", "reverse", "-af", "areverse",
            "-c:v", "libx264", "-crf", "18", str(output),
        ]
    else:
        argv = _base(locator) + [
            "-i", str(source), "-vf", "reverse", "-an",
            "-c:v", "libx264", "-crf", "18", str(output),
        ]
    return FfmpegCommand(argv, output, duration_hint, "Reverse")


def loop_clip(
    locator: FfmpegLocator, source: Path, output: Path, times: int = 3,
    boomerang: bool = False, duration_hint: float = 0.0,
) -> FfmpegCommand:
    if boomerang:
        argv = _base(locator) + [
            "-i", str(source),
            "-filter_complex", "[0:v]split[a][b];[b]reverse[r];[a][r]concat=n=2:v=1[out]",
            "-map", "[out]", "-c:v", "libx264", "-crf", "18", "-an", str(output),
        ]
        return FfmpegCommand(argv, output, duration_hint * 2, "Boomerang")
    argv = _base(locator) + [
        "-stream_loop", str(max(0, times - 1)), "-i", str(source),
        "-c:v", "libx264", "-crf", "18", "-c:a", "aac", str(output),
    ]
    return FfmpegCommand(argv, output, duration_hint * times, f"Loop x{times}")


# ======================================================================================
# Compositing
# ======================================================================================

def chroma_key(
    locator: FfmpegLocator, foreground: Path, background: Path, output: Path,
    key_colour: str = "0x00FF00", similarity: float = 0.30, blend: float = 0.10,
    duration_hint: float = 0.0,
) -> FfmpegCommand:
    """Green-screen composite. `background` may be a still or a video."""
    argv = _base(locator) + [
        "-i", str(background), "-i", str(foreground),
        "-filter_complex",
        f"[1:v]chromakey={key_colour}:{similarity}:{blend},despill[fg];"
        f"[0:v][fg]overlay=(W-w)/2:(H-h)/2:shortest=1[out]",
        "-map", "[out]", "-c:v", "libx264", "-crf", "18", str(output),
    ]
    return FfmpegCommand(argv, output, duration_hint, "Chroma key")


def picture_in_picture(
    locator: FfmpegLocator, main: Path, inset: Path, output: Path,
    position: str = "bottom-right", scale_percent: int = 30, border: int = 3,
    duration_hint: float = 0.0,
) -> FfmpegCommand:
    anchors = {
        "top-left": "20:20", "top-right": "W-w-20:20",
        "bottom-left": "20:H-h-20", "bottom-right": "W-w-20:H-h-20",
    }
    argv = _base(locator) + [
        "-i", str(main), "-i", str(inset),
        "-filter_complex",
        f"[1:v]scale=iw*{scale_percent / 100:.3f}:-2,"
        f"pad=iw+{border * 2}:ih+{border * 2}:{border}:{border}:white[pip];"
        f"[0:v][pip]overlay={anchors.get(position, anchors['bottom-right'])}[out]",
        "-map", "[out]", "-map", "0:a?", "-c:v", "libx264", "-crf", "18",
        "-c:a", "aac", str(output),
    ]
    return FfmpegCommand(argv, output, duration_hint, "Picture in picture")


def split_screen(
    locator: FfmpegLocator, left: Path, right: Path, output: Path,
    orientation: str = "horizontal", width: int = 1920, height: int = 1080,
    duration_hint: float = 0.0,
) -> FfmpegCommand:
    if orientation == "horizontal":
        half_width, half_height, stack = width // 2, height, "hstack"
    else:
        half_width, half_height, stack = width, height // 2, "vstack"
    scale = (f"scale={half_width}:{half_height}:force_original_aspect_ratio=increase,"
             f"crop={half_width}:{half_height},setsar=1")
    argv = _base(locator) + [
        "-i", str(left), "-i", str(right),
        "-filter_complex",
        f"[0:v]{scale}[l];[1:v]{scale}[r];[l][r]{stack}=inputs=2[out]",
        "-map", "[out]", "-c:v", "libx264", "-crf", "18", "-an", str(output),
    ]
    return FfmpegCommand(argv, output, duration_hint, f"Split screen ({orientation})")


def blur_region(
    locator: FfmpegLocator, source: Path, output: Path, x: int, y: int,
    width: int, height: int, strength: int = 20, duration_hint: float = 0.0,
) -> FfmpegCommand:
    """Blurs a rectangle -- faces, plates, logos, on-screen credentials.

    The source video is used twice (as the base and as the crop source), so it must be
    split explicitly: a filter_complex input pad can only feed one consumer.
    """
    # gblur takes a sigma, not a pixel radius, so it cannot exceed the plane size the way
    # boxblur does -- boxblur rejects any chroma radius >= the (half-size) chroma plane,
    # which makes it fail on exactly the small regions this is used for.
    argv = _base(locator) + [
        "-i", str(source),
        "-filter_complex",
        f"[0:v]split=2[base][copy];"
        f"[copy]crop={width}:{height}:{x}:{y},gblur=sigma={max(1, strength)}:steps=3[blurred];"
        f"[base][blurred]overlay={x}:{y}[out]",
        "-map", "[out]", "-map", "0:a?", "-c:v", "libx264", "-crf", "18",
        "-c:a", "copy", str(output),
    ]
    return FfmpegCommand(argv, output, duration_hint, "Blur region")


def default_font() -> str:
    """drawtext needs an explicit font file on Windows.

    Windows ships no fontconfig, so a bare `drawtext` fails with
    'Cannot load default config file' and writes nothing. Point it at a real .ttf.
    """
    candidates: list[Path] = []
    if sys.platform == "win32":
        windows_fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        candidates = [windows_fonts / name for name in
                      ("arial.ttf", "segoeui.ttf", "calibri.ttf", "tahoma.ttf", "verdana.ttf")]
    elif sys.platform == "darwin":
        candidates = [Path("/System/Library/Fonts/Helvetica.ttc"),
                      Path("/Library/Fonts/Arial.ttf")]
    else:
        candidates = [
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/TTF/DejaVuSans.ttf"),
        ]
    return next((str(path) for path in candidates if path.exists()), "")


def _escape_filter_path(path: str) -> str:
    """ffmpeg filter arguments need Windows paths forward-slashed and colons escaped."""
    return path.replace("\\", "/").replace(":", "\\:")


def add_text(
    locator: FfmpegLocator, source: Path, output: Path, text: str,
    position: str = "bottom", font_size: int = 48, colour: str = "white",
    box: bool = True, start: float = 0.0, end: float = 0.0, duration_hint: float = 0.0,
    font_file: str = "",
) -> FfmpegCommand:
    """Burned-in title/caption.

    The text goes through a sidecar file rather than inline. Inline `text=` is a minefield:
    `%` triggers drawtext's strftime expansion and fails the whole render, `:` and `'`
    fight the filter-graph parser, and non-Latin scripts depend on console encoding.
    `textfile=` with `expansion=none` makes all of that go away, and allows multi-line.
    """
    anchors = {
        "top": "x=(w-text_w)/2:y=60", "center": "x=(w-text_w)/2:y=(h-text_h)/2",
        "bottom": "x=(w-text_w)/2:y=h-text_h-60",
        "top-left": "x=60:y=60", "bottom-left": "x=60:y=h-text_h-60",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    sidecar = output.parent / f"{output.stem}.drawtext.txt"
    sidecar.write_text(text, encoding="utf-8")

    chain = f"drawtext=textfile='{_escape_filter_path(str(sidecar))}':expansion=none"
    font = font_file or default_font()
    if font:
        chain += f":fontfile='{_escape_filter_path(font)}'"
    chain += (f":fontsize={font_size}:fontcolor={colour}:"
              f"{anchors.get(position, anchors['bottom'])}")
    if box:
        chain += ":box=1:boxcolor=black@0.5:boxborderw=12"
    if end > start:
        chain += f":enable='between(t,{start},{end})'"
    argv = _base(locator) + [
        "-i", str(source), "-vf", chain, "-c:v", "libx264", "-crf", "18",
        "-c:a", "copy", str(output),
    ]
    return FfmpegCommand(argv, output, duration_hint, "Add text")


def apply_lut(
    locator: FfmpegLocator, source: Path, lut: Path, output: Path,
    duration_hint: float = 0.0,
) -> FfmpegCommand:
    escaped = str(lut).replace("\\", "/").replace(":", "\\:")
    argv = _base(locator) + [
        "-i", str(source), "-vf", f"lut3d='{escaped}'",
        "-c:v", "libx264", "-crf", "18", "-c:a", "copy", str(output),
    ]
    return FfmpegCommand(argv, output, duration_hint, "Apply LUT")


def tonemap_hdr(
    locator: FfmpegLocator, source: Path, output: Path, duration_hint: float = 0.0
) -> FfmpegCommand:
    """HDR -> SDR without the washed-out look a naive convert gives."""
    argv = _base(locator) + [
        "-i", str(source),
        "-vf", "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
               "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p",
        "-c:v", "libx264", "-crf", "18", "-c:a", "copy", str(output),
    ]
    return FfmpegCommand(argv, output, duration_hint, "HDR to SDR")


# ======================================================================================
# Audio
# ======================================================================================

def normalise_audio(
    locator: FfmpegLocator, source: Path, output: Path, target_lufs: float = -14.0,
    duration_hint: float = 0.0,
) -> FfmpegCommand:
    """EBU R128 loudness normalisation. -14 LUFS is the streaming-platform target."""
    argv = _base(locator) + [
        "-i", str(source), "-af", f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", str(output),
    ]
    return FfmpegCommand(argv, output, duration_hint, f"Normalise to {target_lufs} LUFS")


def clean_speech(
    locator: FfmpegLocator, source: Path, output: Path, gate_db: int = -30,
    duration_hint: float = 0.0,
) -> FfmpegCommand:
    """Noise gate + high-pass + de-esser + compressor: the standard dialogue chain."""
    chain = (
        f"highpass=f=80,"
        f"afftdn=nf=-25,"
        f"agate=threshold={10 ** (gate_db / 20):.6f}:ratio=2:attack=10:release=200,"
        f"acompressor=threshold=-18dB:ratio=3:attack=20:release=250,"
        f"alimiter=limit=0.95"
    )
    argv = _base(locator) + [
        "-i", str(source), "-af", chain, "-c:v", "copy", "-c:a", "aac",
        "-b:a", "192k", str(output),
    ]
    return FfmpegCommand(argv, output, duration_hint, "Clean speech")


def measure_loudness(locator: FfmpegLocator, source: Path) -> dict:
    """Runs loudnorm in analysis mode and returns the measured values."""
    locator.require()
    result = subprocess.run(
        [locator.ffmpeg, "-hide_banner", "-i", str(source), "-af",
         "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json", "-f", "null",
         "-" if sys.platform != "win32" else "NUL"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=_NO_WINDOW,
    )
    text = result.stderr or ""
    start = text.rfind("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return {}


# ======================================================================================
# Stills and inspection
# ======================================================================================

def contact_sheet(
    locator: FfmpegLocator, source: Path, output: Path, columns: int = 4, rows: int = 4,
    width: int = 480,
) -> FfmpegCommand:
    """A single grid image of the whole clip -- fast way to find a shot."""
    argv = _base(locator) + [
        "-i", str(source),
        "-vf", f"thumbnail,scale={width}:-1,tile={columns}x{rows}",
        "-frames:v", "1", str(output),
    ]
    return FfmpegCommand(argv, output, 0.0, "Contact sheet")


def convert_image(
    locator: FfmpegLocator, source: Path, output: Path, width: int = 0, height: int = 0,
    quality: int = 92,
) -> FfmpegCommand:
    """Format convert + resize for stills, including webp and avif when the build has them."""
    argv = _base(locator) + ["-i", str(source)]
    if width or height:
        argv += ["-vf", f"scale={width or -1}:{height or -1}:flags=lanczos"]
    suffix = output.suffix.lower()
    if suffix in (".jpg", ".jpeg"):
        argv += ["-q:v", str(max(2, min(31, int(31 - quality * 0.29))))]
    elif suffix == ".webp":
        argv += ["-quality", str(quality)]
    elif suffix == ".png":
        argv += ["-compression_level", "6"]
    argv += [str(output)]
    return FfmpegCommand(argv, output, 0.0, f"Convert to {suffix.lstrip('.')}")


def waveform_image(
    locator: FfmpegLocator, source: Path, output: Path, width: int = 1600, height: int = 400
) -> FfmpegCommand:
    argv = _base(locator) + [
        "-i", str(source),
        "-filter_complex", f"showwavespic=s={width}x{height}:colors=#2f6feb",
        "-frames:v", "1", str(output),
    ]
    return FfmpegCommand(argv, output, 0.0, "Waveform image")


def scene_detect(locator: FfmpegLocator, source: Path, threshold: float = 0.30) -> list[float]:
    """Returns timestamps where the shot changes -- the basis for auto-splitting."""
    locator.require()
    result = subprocess.run(
        [locator.ffmpeg, "-i", str(source), "-vf",
         f"select='gt(scene,{threshold})',showinfo", "-f", "null",
         "-" if sys.platform != "win32" else "NUL"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=_NO_WINDOW,
    )
    timestamps: list[float] = []
    for line in (result.stderr or "").splitlines():
        if "pts_time:" in line:
            try:
                timestamps.append(float(line.split("pts_time:")[1].split()[0]))
            except (IndexError, ValueError):
                continue
    return timestamps
