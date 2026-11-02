"""Quick clip: photos in, a film of the asked length out, faithful to the photos."""

from __future__ import annotations

import pytest

from mediaforge import fidelity, ffmpeg_tools, quickfilm

from conftest import wait_until


def test_length_is_spread_evenly_and_capped_at_three_minutes():
    each = quickfilm.seconds_per_photo(180, 30)
    assert abs(30 * each - quickfilm.CROSSFADE * 29 - 180) < 0.5
    assert quickfilm.seconds_per_photo(600, 30) == quickfilm.seconds_per_photo(180, 30)
    assert quickfilm.seconds_per_photo(180, 2) == quickfilm.PER_PHOTO_RANGE[1]
    assert quickfilm.default_length(100) == quickfilm.MAX_SECONDS


def test_ai_is_spread_over_the_film():
    assert quickfilm.ai_shot_indices(10, "off") == []
    assert quickfilm.ai_shot_indices(10, "all") == list(range(10))
    some = quickfilm.ai_shot_indices(10, "some")
    assert len(some) == 3 and some[0] > 0 and some[-1] < 9


def test_the_plan_keeps_the_photos_as_they_are(photos, window):
    quick = quickfilm.plan(photos, window.context.registry, 60, "off")
    shots = quick.plan.shots
    assert [shot.path for shot in shots] == list(photos), "the order given is kept"
    assert all(shot.atmosphere == "None" and not shot.vignette and not shot.grain
               for shot in shots), "no looks, particles, vignette or grain"
    assert abs(quick.length - 60) <= 1.0


def test_quick_clip_renders_the_length_asked(window, qapp, photos, locator):
    panel = window.quick_panel
    window.go_to("quick")
    job = panel.start(list(photos), 20.0, "off", "auto", None, "")
    assert wait_until(qapp, lambda: job.state.value in ("done", "failed"), 180), job.message
    assert job.state.value == "done", job.error
    assert abs(ffmpeg_tools.probe(locator, job.result["path"]).duration - 20.0) < 1.0
    assert panel.result_card.isVisibleTo(panel)


def test_a_faithful_clip_passes_and_a_changed_one_does_not(tmp_path, locator):
    """A slowly zooming photo passes; the same photo with a block pasted in partway fails."""
    import subprocess

    from PIL import Image, ImageDraw

    photo = tmp_path / "scene.png"
    image = Image.new("RGB", (640, 360))
    draw = ImageDraw.Draw(image)
    for x in range(0, 640, 40):
        draw.rectangle((x, 0, x + 20, 360), fill=(40 + x // 4, 120, 200 - x // 4))
    image.save(photo)
    faithful = tmp_path / "faithful.mp4"
    subprocess.run([locator.ffmpeg, "-y", "-v", "error", "-loop", "1", "-i", str(photo),
                    "-vf", "zoompan=z='1+0.0005*on':d=1:s=640x360:fps=24", "-frames:v", "48",
                    "-pix_fmt", "yuv420p", str(faithful)], check=True)
    changed = tmp_path / "changed.mp4"
    subprocess.run([locator.ffmpeg, "-y", "-v", "error", "-loop", "1", "-i", str(photo),
                    "-vf", "drawbox=x=420:y=120:w=90:h=150:color=white:t=fill:"
                           "enable='gte(t,1)'", "-t", "2", "-r", "24",
                    "-pix_fmt", "yuv420p", str(changed)], check=True)
    assert fidelity.check(locator, photo, faithful).faithful
    verdict = fidelity.check(locator, photo, changed)
    assert not verdict.faithful and "appeared" in verdict.reason


@pytest.mark.parametrize("key", ["quick"])
def test_quick_clip_is_on_the_home_screen_and_in_the_sidebar(window, key):
    assert key in window.panels
    assert key in window.sidebar.buttons
