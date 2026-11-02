"""Photos in, moving clip out.

Every photo carries its own plan: how long it holds, how the camera moves, what the air
does, and whether the motion comes from ffmpeg (free, 2D) or from an image-to-video model
(costly, real 3D parallax). The pipeline renders each shot, crossfades them, and lays
music underneath.

Two details separate this from a slideshow:

  1. zoompan steps `x`/`y` in whole pixels of the *source*, so a small source stair-steps.
     Every photo is upscaled before the move and scaled back after, turning those steps
     into sub-pixel motion.
  2. zoompan emits `d` frames for EVERY frame it is fed. Feeding it a looping still with
     `-t` replays the move fps*seconds times. The output frame count is bounded instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from . import atmosphere as atmo
from . import ffmpeg_tools
from .ffmpeg_tools import FfmpegCommand, FfmpegLocator

# Motion styles, in the order "Auto" cycles through them when there is no analysis.
MOTION_STYLES = [
    "Zoom in",
    "Pan right",
    "Zoom out",
    "Pan left",
    "Zoom in + pan up",
    "Pan down",
]

ALL_MOVES = ["Auto"] + MOTION_STYLES + ["Pan up", "Still (no motion)"]

STYLE_HELP = {
    "Auto": "Chosen per photo from what the picture actually contains.",
    "Auto (varied)": "Cycles through the moves below so no two shots feel the same.",
    "Zoom in": "Slow push toward the centre. The safe, classic choice.",
    "Zoom out": "Starts tight and reveals the frame. Good for aerials.",
    "Pan right": "Drifts across the photo left to right.",
    "Pan left": "Drifts across the photo right to left.",
    "Pan up": "Rises up the frame. Good for tall buildings.",
    "Pan down": "Falls down the frame.",
    "Zoom in + pan up": "Push in while rising. Good for landscapes.",
    "Still (no motion)": "No movement at all.",
}

TRANSFORMS = ["Auto", "Camera move (free)", "AI video", "Still"]

TRANSFORM_HELP = {
    "Auto": "Uses AI video when a model is available and the shot would gain from real "
            "3D movement; falls back to a camera move otherwise.",
    "Camera move (free)": "ffmpeg zoom and pan over the still. Instant, free, offline. "
                          "2D only — no parallax.",
    "AI video": "Sends the photo to an image-to-video model for true 3D camera movement, "
                "moving skies and people. Costs money and takes minutes.",
    "Still": "Hold the photo with no movement.",
}

ASPECTS = {
    "16:9 landscape": (1920, 1080),
    "9:16 vertical": (1080, 1920),
    "1:1 square": (1080, 1080),
    "4:5 portrait": (1080, 1350),
    "21:9 cinematic": (2560, 1080),
}

FIT_MODES = {
    "Fill the frame (crop edges)": "fill",
    "Fit whole photo (blurred edges)": "blur",
    "Fit whole photo (black bars)": "pad",
}


@dataclass
class Shot:
    """One photo and everything decided about it."""
    path: Path
    seconds: float = 4.0
    move: str = "Auto"
    motion_strength: float = 0.18
    atmosphere: str = "None"
    atmosphere_intensity: float = 0.5
    vignette: bool = False
    grain: float = 0.0
    transform: str = "Auto"
    ai_provider: str = ""            # "" means let the app choose
    ai_model: str = ""
    ai_prompt: str = ""
    analysis: object | None = None   # SceneAnalysis, kept loose to avoid a hard import
    resolved_transform: str = ""     # filled in during render, for reporting
    note: str = ""
    ai_static_camera: bool = False   # the AI clip animates the scene, not the camera

    def label(self) -> str:
        return self.path.name


@dataclass
class PhotoFilmSettings:
    aspect: str = "16:9 landscape"
    seconds_each: float = 4.0
    crossfade: float = 0.8
    fps: int = 30
    motion: str = "Auto"
    motion_strength: float = 0.18
    fit: str = "Fill the frame (crop edges)"
    atmosphere: str = "None"
    music: Path | None = None
    music_volume: float = 0.5
    fade_in: float = 0.6
    fade_out: float = 1.2
    quality_crf: int = 19
    seed: int = 7

    @property
    def size(self) -> tuple[int, int]:
        return ASPECTS.get(self.aspect, (1920, 1080))

    @property
    def fit_mode(self) -> str:
        return FIT_MODES.get(self.fit, "fill")

    def total_seconds(self, shots: Sequence[Shot] | int) -> float:
        if isinstance(shots, int):
            count, span = shots, self.seconds_each * shots
        else:
            count, span = len(shots), sum(shot.seconds for shot in shots)
        if count <= 0:
            return 0.0
        return max(0.1, span - self.crossfade * max(0, count - 1))


def _fit_chain(width: int, height: int, mode: str, upscale: int) -> str:
    big_width, big_height = width * upscale, height * upscale
    if mode == "fill":
        return (f"scale={big_width}:{big_height}:force_original_aspect_ratio=increase,"
                f"crop={big_width}:{big_height}")
    if mode == "pad":
        return (f"scale={big_width}:{big_height}:force_original_aspect_ratio=decrease,"
                f"pad={big_width}:{big_height}:(ow-iw)/2:(oh-ih)/2:color=black")
    return (
        f"split=2[bg][fg];"
        f"[bg]scale={big_width}:{big_height}:force_original_aspect_ratio=increase,"
        f"crop={big_width}:{big_height},gblur=sigma={max(8, upscale * 12)}[bgb];"
        f"[fg]scale={big_width}:{big_height}:force_original_aspect_ratio=decrease[fgs];"
        f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2"
    )


def motion_expression(style: str, frames: int, strength: float) -> tuple[str, str, str]:
    """(zoom, x, y) zoompan expressions. `on` is the output frame counter, so on/frames
    is a 0..1 progress ramp."""
    frames = max(2, frames)
    ramp = f"(on/{frames})"
    zoom_max = 1.0 + max(0.02, strength)
    centre_x = "iw/2-(iw/zoom/2)"
    centre_y = "ih/2-(ih/zoom/2)"

    if style == "Zoom in":
        return f"1+{strength:.4f}*{ramp}", centre_x, centre_y
    if style == "Zoom out":
        return f"{zoom_max:.4f}-{strength:.4f}*{ramp}", centre_x, centre_y
    if style == "Pan right":
        return f"{zoom_max:.4f}", f"(iw-iw/zoom)*{ramp}", centre_y
    if style == "Pan left":
        return f"{zoom_max:.4f}", f"(iw-iw/zoom)*(1-{ramp})", centre_y
    if style == "Pan up":
        return f"{zoom_max:.4f}", centre_x, f"(ih-ih/zoom)*(1-{ramp})"
    if style == "Pan down":
        return f"{zoom_max:.4f}", centre_x, f"(ih-ih/zoom)*{ramp}"
    if style == "Zoom in + pan up":
        return f"1+{strength:.4f}*{ramp}", centre_x, f"(ih-ih/zoom)*(1-{ramp})"
    return "1", centre_x, centre_y


def resolve_move(shot: Shot, index: int) -> str:
    """Turns 'Auto' into a concrete move: the analysis if there is one, else a cycle."""
    if shot.move and shot.move not in ("Auto", "Auto (varied)"):
        return shot.move
    analysis = shot.analysis
    if analysis is not None and getattr(analysis, "move", ""):
        return analysis.move
    return MOTION_STYLES[index % len(MOTION_STYLES)]


def build_shot(
    locator: FfmpegLocator, shot: Shot, output: Path, settings: PhotoFilmSettings, index: int
) -> FfmpegCommand:
    """One still -> one moving, graded clip."""
    locator.require()
    width, height = settings.size
    frames = max(2, int(shot.seconds * settings.fps))
    move = "Still (no motion)" if shot.transform == "Still" else resolve_move(shot, index)
    zoom, x, y = motion_expression(move, frames, shot.motion_strength)

    upscale = 2
    fit = _fit_chain(width, height, settings.fit_mode, upscale)

    base = (
        f"[0:v]{fit},"
        f"zoompan=z='{zoom}':x='{x}':y='{y}':d={frames}:s={width}x{height}:fps={settings.fps},"
        f"setsar=1,format=yuv420p[base]"
    )

    chain, wants_source = atmo.build_chain(
        shot.atmosphere, width, height, settings.fps, shot.atmosphere_intensity,
        vignette=shot.vignette, grain=shot.grain,
    )

    argv = [locator.ffmpeg, "-y", "-hide_banner", "-loop", "1", "-i", str(shot.path)]
    graph = base + ";"
    if wants_source:
        # A synthetic noise plate feeds the weather layer -- no external asset needed.
        argv += [
            "-f", "lavfi", "-i",
            f"nullsrc=s={width}x{height * 2}:r={settings.fps},noise=alls=96:allf=t+u",
        ]
        graph += "[1:v]null[atmo_src];" + chain + "[vout]"
    else:
        graph += chain + "[vout]"

    argv += [
        "-filter_complex", graph,
        "-map", "[vout]",
        "-frames:v", str(frames),
        "-r", str(settings.fps),
        "-c:v", "libx264", "-crf", str(settings.quality_crf), "-preset", "medium",
        "-pix_fmt", "yuv420p",
        str(output),
    ]
    return FfmpegCommand(argv, output, shot.seconds, f"Shot {index + 1}: {move}")


# How far an AI clip may be slowed to fill its shot before it loops instead. Beyond about
# 1.25x, slowed foliage and water start to look like a different speed.
MAX_STRETCH = 1.25


def build_conform(
    locator: FfmpegLocator, source: Path, output: Path, settings: PhotoFilmSettings,
    shot: Shot, index: int, source_seconds: float = 0.0,
) -> FfmpegCommand:
    """Brings an AI-generated clip to the project's canvas, fps and duration.

    Two things a clip from a local model needs that a cloud clip usually does not:

      * **Length.** The local model makes at most 3.4 s, and a shot is often 4-5 s -- in a
        three-minute film from a few photos, 20 s or more. An output shorter than its
        shot would throw every later crossfade off, so the clip is slowed a little and,
        if still short, plays forward then backward until the shot ends. Holding the last
        frame, as this used to, froze the scene for most of a long shot.
      * **Camera.** It animates the scene but barely moves the camera (measured: no
        global shift first to last frame). `shot.ai_static_camera` adds the shot's own
        camera move on top, smoothed the same way as a still's.
    """
    locator.require()
    width, height = settings.size
    frames = max(2, int(shot.seconds * settings.fps))

    chain, wants_source = atmo.build_chain(
        shot.atmosphere, width, height, settings.fps, shot.atmosphere_intensity,
        vignette=shot.vignette, grain=shot.grain,
    )

    timing = ""
    if 0 < source_seconds < shot.seconds:
        stretch = min(shot.seconds / source_seconds, MAX_STRETCH)
        short_by = shot.seconds - source_seconds * stretch
        timing = f"setpts={stretch:.4f}*PTS,fps={settings.fps},"
        if short_by > 0.01:
            # Forward, then backward without repeating the turning frame, looped: the
            # motion never stops and never jumps back to the start.
            frames_once = max(2, int(source_seconds * stretch * settings.fps))
            timing += ("split[ahead][back];[back]reverse,trim=start_frame=1[reversed];"
                       "[ahead][reversed]concat=n=2:v=1:a=0,"
                       f"loop=loop=-1:size={2 * frames_once - 1}:start=0,"
                       f"setpts=N/{settings.fps}/TB,")
    else:
        timing = f"fps={settings.fps},"

    if shot.ai_static_camera:
        move = "Still (no motion)" if shot.transform == "Still" else resolve_move(shot, index)
        # Gentler than on a still: the scene already moves, and the two add up.
        zoom, x, y = motion_expression(move, frames, shot.motion_strength * 0.7)
        framing = (
            f"{_fit_chain(width, height, 'fill', 2)},"
            f"zoompan=z='{zoom}':x='{x}':y='{y}':d=1:s={width}x{height}:fps={settings.fps},"
        )
    else:
        framing = (f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                   f"crop={width}:{height},")

    argv = [locator.ffmpeg, "-y", "-hide_banner", "-i", str(source)]
    base = f"[0:v]{timing}{framing}setsar=1,format=yuv420p[base]"
    graph = base + ";"
    if wants_source:
        argv += [
            "-f", "lavfi", "-i",
            f"nullsrc=s={width}x{height * 2}:r={settings.fps},noise=alls=96:allf=t+u",
        ]
        graph += "[1:v]null[atmo_src];" + chain + "[vout]"
    else:
        graph += chain + "[vout]"

    argv += [
        "-filter_complex", graph, "-map", "[vout]",
        "-frames:v", str(frames), "-r", str(settings.fps),
        "-c:v", "libx264", "-crf", str(settings.quality_crf), "-preset", "medium",
        "-pix_fmt", "yuv420p", "-an", str(output),
    ]
    return FfmpegCommand(argv, output, shot.seconds, f"Shot {index + 1}: conform AI clip")


def build_join(
    locator: FfmpegLocator, clips: Sequence[Path], shots: Sequence[Shot], output: Path,
    settings: PhotoFilmSettings,
) -> FfmpegCommand:
    locator.require()
    if not clips:
        raise ValueError("No shots to join.")

    argv = [locator.ffmpeg, "-y", "-hide_banner"]
    for clip in clips:
        argv += ["-i", str(clip)]

    total = settings.total_seconds(list(shots))

    if len(clips) == 1:
        filters = []
        video_out = "0:v"
    else:
        filters = []
        if settings.crossfade > 0:
            current = "0:v"
            offset = shots[0].seconds - settings.crossfade
            for index in range(1, len(clips)):
                label = f"x{index}"
                filters.append(
                    f"[{current}][{index}:v]xfade=transition=fade:"
                    f"duration={settings.crossfade}:offset={max(0.05, offset):.3f}[{label}]"
                )
                current = label
                offset += shots[index].seconds - settings.crossfade
            video_out = current
        else:
            joined = "".join(f"[{index}:v]" for index in range(len(clips)))
            filters.append(f"{joined}concat=n={len(clips)}:v=1:a=0[vout]")
            video_out = "vout"

    if settings.fade_in > 0 or settings.fade_out > 0:
        fade_start = max(0.0, total - settings.fade_out)
        filters.append(
            f"[{video_out}]fade=t=in:st=0:d={settings.fade_in},"
            f"fade=t=out:st={fade_start:.3f}:d={settings.fade_out}[faded]"
        )
        video_out = "faded"

    if filters:
        argv += ["-filter_complex", ";".join(filters), "-map", f"[{video_out}]"]
    else:
        argv += ["-map", f"{video_out}"]

    argv += [
        "-c:v", "libx264", "-crf", str(settings.quality_crf), "-preset", "medium",
        "-pix_fmt", "yuv420p", "-r", str(settings.fps), str(output),
    ]
    return FfmpegCommand(argv, output, total, f"Joining {len(clips)} shots")


def build_music_mix(
    locator: FfmpegLocator, video: Path, music: Path, output: Path,
    settings: PhotoFilmSettings, total: float,
) -> FfmpegCommand:
    locator.require()
    fade_start = max(0.0, total - settings.fade_out - 0.4)
    argv = [
        locator.ffmpeg, "-y", "-hide_banner",
        # A track shorter than the film repeats rather than leaving the end silent.
        "-i", str(video), "-stream_loop", "-1", "-i", str(music),
        "-filter_complex",
        f"[1:a]atrim=0:{total:.3f},asetpts=PTS-STARTPTS,"
        f"volume={settings.music_volume:.3f},"
        f"afade=t=in:st=0:d=1.2,afade=t=out:st={fade_start:.3f}:d=1.4[a]",
        "-map", "0:v", "-map", "[a]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
        str(output),
    ]
    return FfmpegCommand(argv, output, total, "Adding music")


def render(
    locator: FfmpegLocator,
    shots: Sequence[Shot],
    output: Path,
    settings: PhotoFilmSettings,
    work_dir: Path,
    progress: Callable[[float, str], None] = lambda fraction, message: None,
    should_cancel: Callable[[], bool] | None = None,
    ai_renderer: Callable[..., Path | None] | None = None,
) -> Path:
    """Renders the whole film. `ai_renderer(shot, index, output_dir, progress, cancel)`
    returns a clip path, or None to fall back to a camera move."""
    if not shots:
        raise ValueError("Add at least one photo.")
    locator.require()

    work_dir.mkdir(parents=True, exist_ok=True)
    for stale in work_dir.glob("shot_*.mp4"):
        stale.unlink(missing_ok=True)

    clips: list[Path] = []
    share = 0.72 / len(shots)

    for index, shot in enumerate(shots):
        if should_cancel and should_cancel():
            raise RuntimeError("Cancelled")
        base_progress = 0.03 + share * index
        target = work_dir / f"shot_{index:03d}.mp4"

        wants_ai = shot.transform == "AI video" or (
            shot.transform == "Auto" and shot.resolved_transform == "AI video"
        )
        produced: Path | None = None

        if wants_ai and ai_renderer is not None:
            progress(base_progress, f"Photo {index + 1}: asking the AI model")
            try:
                raw = ai_renderer(
                    shot, index, work_dir,
                    lambda fraction, message, b=base_progress: progress(
                        b + share * 0.8 * fraction, message
                    ),
                    should_cancel,
                )
            except Exception as error:
                raw = None
                shot.note = f"AI failed ({str(error)[:120]}); used a camera move instead."
            if raw is not None and Path(raw).exists():
                progress(base_progress + share * 0.85, f"Photo {index + 1}: conforming")
                try:
                    source_seconds = ffmpeg_tools.probe(locator, Path(raw)).duration
                except Exception:          # an unreadable length only loses the stretch
                    source_seconds = 0.0
                command = build_conform(locator, Path(raw), target, settings, shot, index,
                                        source_seconds)
                code, log = ffmpeg_tools.run_with_progress(
                    command, should_cancel=should_cancel
                )
                if code == 0:
                    produced = target
                    shot.resolved_transform = "AI video"
                else:
                    shot.note = f"Could not conform the AI clip; used a camera move.\n{log[-300:]}"

        if produced is None:
            if wants_ai:
                # Said honestly in the report and in what is learned: this one did not move.
                shot.resolved_transform = "Camera move"
            progress(base_progress, f"Photo {index + 1} of {len(shots)}: camera move")
            command = build_shot(locator, shot, target, settings, index)
            code, log = ffmpeg_tools.run_with_progress(
                command,
                on_progress=lambda fraction, label, b=base_progress: progress(
                    b + share * fraction, label
                ),
                should_cancel=should_cancel,
            )
            if code != 0:
                raise RuntimeError(
                    f"Could not animate '{shot.path.name}'.\n"
                    f"It may be a format ffmpeg cannot read (HEIC often is).\n\n{log[-500:]}"
                )
            produced = target
            if not shot.resolved_transform:
                shot.resolved_transform = "Camera move"

        clips.append(produced)

    progress(0.78, "Joining shots with crossfades")
    silent = work_dir / "joined.mp4"
    join = build_join(locator, clips, shots, silent, settings)
    code, log = ffmpeg_tools.run_with_progress(
        join,
        on_progress=lambda fraction, label: progress(0.78 + 0.15 * fraction, f"Joining {label}"),
        should_cancel=should_cancel,
    )
    if code != 0:
        raise RuntimeError(f"Joining the shots failed:\n{log[-800:]}")

    total = settings.total_seconds(list(shots))
    output.parent.mkdir(parents=True, exist_ok=True)

    if settings.music and settings.music.exists():
        progress(0.94, "Adding music")
        mix = build_music_mix(locator, silent, settings.music, output, settings, total)
        code, log = ffmpeg_tools.run_with_progress(mix, should_cancel=should_cancel)
        if code != 0:
            raise RuntimeError(f"Adding music failed:\n{log[-800:]}")
    else:
        output.unlink(missing_ok=True)
        silent.replace(output)

    for clip in clips:
        clip.unlink(missing_ok=True)
    silent.unlink(missing_ok=True)

    progress(1.0, "Finished")
    return output
