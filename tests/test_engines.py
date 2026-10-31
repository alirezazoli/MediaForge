"""The media engines on real (generated) files: measure, cut, render."""

from __future__ import annotations

from mediaforge import repair
from mediaforge.ffmpeg_tools import probe
from mediaforge.photofilm import PhotoFilmSettings, Shot, render


def test_scan_finds_the_dead_opening(locator, faulty_clip):
    report = repair.scan(locator, faulty_clip)
    assert len(report.dead) == 1
    assert report.dead[0].start < 0.1
    assert abs(report.dead[0].end - 2.0) < 0.3
    assert report.measurement.samples > 0, "frames must be sampled outside the black span"


def test_a_clean_clip_has_nothing_to_cut(locator, plain_clip):
    report = repair.scan(locator, plain_clip)
    assert report.dead == []


def test_switching_the_cut_rule_needs_no_second_scan(locator, faulty_clip):
    report = repair.scan(locator, faulty_clip)
    for mode in repair.CUT_MODES:
        report.recompute(mode)
        assert report.mode == mode
    report.recompute(repair.MODE_DEAD_AIR)
    assert len(report.dead) == 1


def test_photos_render_into_a_film_of_the_right_shape(locator, photos, tmp_path):
    settings = PhotoFilmSettings(aspect="16:9 landscape", seconds_each=1.5, crossfade=0.4, fps=24)
    output = tmp_path / "film.mp4"
    render(locator, [Shot(path=p, seconds=1.5) for p in photos], output, settings,
           tmp_path / "work")
    info = probe(locator, output)
    assert (info.width, info.height) == settings.size
    # three shots of 1.5 s, overlapping by two crossfades of 0.4 s
    assert abs(info.duration - (3 * 1.5 - 2 * 0.4)) < 0.3
