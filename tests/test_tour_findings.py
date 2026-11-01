"""Bugs the feature tour (tools/feature_tour.py) found by running every tool for real."""

from __future__ import annotations

import subprocess

import pytest

from mediaforge import ffmpeg_extra, ffmpeg_tools
from mediaforge.project import Project


def test_adding_a_known_file_returns_that_files_id(tmp_path, plain_clip, faulty_clip):
    """The upsert updated instead of inserting, and lastrowid still held the previous
    insert's id -- so opening a file already in the library opened another file."""
    project = Project(tmp_path / "project")
    first = project.add_asset(plain_clip, "video")
    second = project.add_asset(faulty_clip, "video")
    assert first != second
    assert project.add_asset(plain_clip, "video") == first
    assert project.asset(project.add_asset(plain_clip, "video")).path == plain_clip
    project.close()


def test_speed_works_on_a_clip_with_no_sound(locator, media_dir):
    silent = media_dir / "silent.mp4"
    subprocess.run([locator.ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=320x240:rate=25:duration=2", "-pix_fmt", "yuv420p",
                    str(silent)], check=True)
    output = media_dir / "silent_fast.mp4"
    command = ffmpeg_tools.speed_change(locator, silent, output, 2.0, 2.0,
                                        has_audio=ffmpeg_tools.probe(locator, silent).has_audio)
    code, log = ffmpeg_tools.run_with_progress(command)
    assert code == 0, log
    assert abs(ffmpeg_tools.probe(locator, output).duration - 1.0) < 0.2


def test_a_photo_has_no_duration(locator, photos):
    """ffprobe gives a JPEG one frame's 0.04 s; a template then played each photo for a
    blink and the whole timeline came out 0.2 s long."""
    assert ffmpeg_tools.probe(locator, photos[0]).duration == 0.0


def test_music_goes_under_a_video_that_has_no_sound(locator, media_dir, plain_clip):
    silent = media_dir / "silent_for_music.mp4"
    subprocess.run([locator.ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=320x240:rate=25:duration=2", "-pix_fmt", "yuv420p",
                    str(silent)], check=True)
    output = media_dir / "silent_with_music.mp4"
    command = ffmpeg_tools.mux_audio(locator, silent, plain_clip, output, 2.0,
                                     keep_original=True, music_volume=0.35,
                                     video_has_audio=False)
    code, log = ffmpeg_tools.run_with_progress(command)
    assert code == 0, log
    assert ffmpeg_tools.probe(locator, output).has_audio


def test_hdr_to_sdr_says_an_sdr_clip_needs_nothing(window, plain_clip):
    """It used to hand an SDR clip to zscale, which failed with 'Generic error in an
    external library'."""
    assert ffmpeg_extra.color_transfer(window.context.locator, plain_clip) not in \
        ffmpeg_extra.HDR_TRANSFERS
    panel = window.edit_panel
    with pytest.raises(ValueError, match="not HDR"):
        panel._build_command("tonemap", {}, plain_clip, 4.0,
                             lambda suffix, tag: plain_clip.with_name(f"x_{tag}{suffix}"),
                             window.context.locator)
