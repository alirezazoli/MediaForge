"""Convert, compress, join and the other delivery tools, run for real with ffmpeg."""

from __future__ import annotations

import subprocess

import pytest

from mediaforge import ffmpeg_extra, ffmpeg_tools

from conftest import wait_until


def _run(command):
    code, log = ffmpeg_tools.run_with_progress(command)
    assert code == 0, log
    return command.output


def _streams(locator, path) -> list[str]:
    result = subprocess.run([locator.ffprobe, "-v", "error", "-show_entries",
                             "stream=codec_type,codec_name", "-of", "csv=p=0", str(path)],
                            capture_output=True, text=True)
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


@pytest.fixture(scope="module")
def speech_clip(locator, media_dir):
    """Ten seconds of 1280x720 picture with sound: enough to make sizes meaningful."""
    path = media_dir / "ten_seconds.mp4"
    subprocess.run([locator.ffmpeg, "-y", "-v", "error",
                    "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=10",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
                    "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(path)], check=True)
    return path


@pytest.mark.parametrize("target", list(ffmpeg_extra.CONVERT_TARGETS))
def test_every_format_converts(locator, media_dir, plain_clip, target):
    spec = ffmpeg_extra.CONVERT_TARGETS[target]
    output = media_dir / f"converted_{abs(hash(target))}{spec['container']}"
    _run(ffmpeg_extra.convert_media(locator, plain_clip, output, target, "Small",
                                    duration_hint=4.0))
    kinds = [line.split(",")[1] if "," in line else line for line in
             _streams(locator, output)]
    assert ("video" in kinds) == (spec["video"] is not None), kinds
    assert "audio" in kinds
    assert abs(ffmpeg_tools.probe(locator, output).duration - 4.0) < 0.3


def test_a_sound_file_is_not_converted_to_video(locator, media_dir, plain_clip):
    sound = _run(ffmpeg_tools.extract_audio(locator, plain_clip, media_dir / "only_sound.mp3"))
    with pytest.raises(ValueError, match="sound only"):
        ffmpeg_extra.convert_media(locator, sound, media_dir / "x.mp4",
                                   "MP4 · H.264 (plays everywhere)", has_video=False)


def test_compress_lands_under_the_size(locator, media_dir, speech_clip):
    info = ffmpeg_tools.probe(locator, speech_clip)
    target_mb = 0.6
    plan = ffmpeg_extra.compress_plan(info.duration, target_mb, info.width, info.height,
                                      info.fps, info.has_audio)
    output = media_dir / "compressed.mp4"
    log = media_dir / "compress_2pass"
    for number in (1, 2):
        _run(ffmpeg_extra.compress_pass(locator, speech_clip, output, plan, number, log))
    size_mb = output.stat().st_size / (1024 * 1024)
    assert size_mb <= target_mb * 1.03, size_mb
    assert size_mb >= target_mb * 0.5, "it should use the room it was given"


def test_compress_refuses_an_impossible_size():
    with pytest.raises(ValueError, match="too small"):
        ffmpeg_extra.compress_plan(600, 1.0, 1920, 1080, 30)


def test_compress_scales_down_rather_than_starving_the_picture():
    plan = ffmpeg_extra.compress_plan(300, 25, 3840, 2160, 30)
    assert plan["height"] and plan["height"] < 2160


def test_join_keeps_sound_and_crossfades(locator, media_dir, plain_clip, faulty_clip):
    durations = [ffmpeg_tools.probe(locator, p).duration for p in (plain_clip, faulty_clip)]
    output = _run(ffmpeg_extra.join_clips(locator, [plain_clip, faulty_clip],
                                          media_dir / "joined.mp4", durations, [True, True],
                                          640, 360, 25, crossfade=0.5))
    info = ffmpeg_tools.probe(locator, output)
    assert info.has_audio
    assert abs(info.duration - (sum(durations) - 0.5)) < 0.3


def test_join_gives_a_silent_clip_silence(locator, media_dir, plain_clip):
    silent = _run(ffmpeg_extra.remove_audio(locator, plain_clip, media_dir / "silent_join.mp4"))
    assert not ffmpeg_tools.probe(locator, silent).has_audio
    output = _run(ffmpeg_extra.join_clips(locator, [plain_clip, silent],
                                          media_dir / "joined_silent.mp4", [4.0, 4.0],
                                          [True, False], 640, 360, 25))
    assert ffmpeg_tools.probe(locator, output).has_audio
    assert abs(ffmpeg_tools.probe(locator, output).duration - 8.0) < 0.3


def _mean_volume(locator, path) -> float:
    result = subprocess.run([locator.ffmpeg, "-hide_banner", "-i", str(path), "-af",
                             "volumedetect", "-vn", "-f", "null", "-"],
                            capture_output=True, text=True)
    line = next(line for line in result.stderr.splitlines() if "mean_volume" in line)
    return float(line.split("mean_volume:")[1].split("dB")[0])


def test_volume_changes_by_the_amount_asked(locator, media_dir, plain_clip):
    quieter = _run(ffmpeg_extra.change_volume(locator, plain_clip,
                                              media_dir / "quieter.mp4", -6.0))
    assert abs((_mean_volume(locator, plain_clip) - _mean_volume(locator, quieter)) - 6.0) < 1.0


def test_snapshot_is_full_size(locator, media_dir, plain_clip):
    still = _run(ffmpeg_extra.snapshot(locator, plain_clip, media_dir / "still.png", 2.0))
    from PIL import Image
    assert Image.open(still).size == (640, 360)


def test_subtitle_track_is_switchable_not_burned(locator, media_dir, plain_clip):
    srt = media_dir / "track.srt"
    srt.write_text("1\n00:00:00,500 --> 00:00:02,000\nHello\n", encoding="utf-8")
    output = _run(ffmpeg_extra.add_subtitle_track(locator, plain_clip, srt,
                                                  media_dir / "with_track.mp4"))
    assert any(line.endswith("subtitle") or line.startswith("mov_text")
               for line in _streams(locator, output)), _streams(locator, output)


def test_animated_webp(locator, media_dir, plain_clip):
    output = _run(ffmpeg_extra.to_animated_webp(locator, plain_clip,
                                                media_dir / "loop.webp", 10, 320))
    assert output.read_bytes()[8:12] == b"WEBP"


def test_library_converts_a_selection_in_one_job(window, qapp, plain_clip, faulty_clip):
    context = window.context
    project = context.require_project()
    assets = [project.asset(context.catalogue(path, source="import"))
              for path in (plain_clip, faulty_clip)]
    before = {job.id for job in context.jobs.jobs()}
    window.library_panel.convert_assets(assets, "MP3 (sound only)", "Small")
    job = next(job for job in context.jobs.jobs() if job.id not in before)
    assert wait_until(qapp, lambda: job.state.value in ("done", "failed"), 60)
    assert job.state.value == "done", job.error
    for asset in assets:
        assert (project.renders_dir / f"{asset.path.stem}.mp3").is_file()


def test_the_gpu_encoder_is_tried_not_assumed(locator):
    """ffmpeg lists h264_nvenc on this PC, but the driver is too old to open it."""
    works, reason = ffmpeg_extra.hardware_encoder_works(locator, "nvidia")
    assert works or reason, "a refusal has to say why"


def test_convert_on_the_gpu_says_why_it_cannot(window, plain_clip, monkeypatch):
    monkeypatch.setattr(ffmpeg_extra, "hardware_encoder_works",
                        lambda locator, vendor: (False, "Update the NVIDIA driver to 610.00"))
    with pytest.raises(ValueError, match="driver"):
        window.edit_panel._build_command(
            "convert", {"target": "MP4 · H.264 (plays everywhere)", "quality": "Balanced",
                        "gpu": True},
            plain_clip, 4.0, lambda suffix, tag: plain_clip.with_name(f"x_{tag}{suffix}"),
            window.context.locator)


def test_edit_offers_the_new_tools(window):
    from mediaforge.ui import edit_panel
    for key in ("convert", "compress", "join", "volume", "mute", "snapshot", "softsubs"):
        assert key in edit_panel.TOOLS
        assert key in window.edit_panel._pages
