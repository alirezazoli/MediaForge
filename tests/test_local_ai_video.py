"""Generated motion on this PC: routing, the Wan 2.2 graph, and fitting clips into a film.

The live test at the bottom talks to a real ComfyUI and takes minutes on a laptop GPU, so
it runs only with MEDIAFORGE_LIVE_COMFYUI=1. Everything else uses a stand-in.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from mediaforge import autopilot
from mediaforge.availability import State, model_availability
from mediaforge.config import Credentials, Settings
from mediaforge.ffmpeg_tools import probe
from mediaforge.photofilm import PhotoFilmSettings, Shot, build_conform
from mediaforge.providers import Capability, ProviderError, build_registry
from mediaforge.providers import comfy_builtin
from mediaforge.providers.comfy_builtin import WAN22_ID


@pytest.fixture()
def registry(monkeypatch):
    built = build_registry(Credentials(), Settings())
    for provider in built:            # no cloud keys, whatever the environment holds
        provider.api_key = ""
    return built


def _comfy(registry, monkeypatch, running: bool, files: bool):
    comfy = registry.get("comfyui")
    monkeypatch.setattr(type(comfy), "reachable", lambda self, force=False: running)
    monkeypatch.setattr(comfy_builtin, "find_wan22_files",
                        lambda base, force=False: comfy_builtin.WanFiles("u", "t", "v")
                        if files else None)
    return comfy


# -- availability and routing -------------------------------------------------------------
def test_built_in_wan_is_chosen_when_comfyui_has_the_files(registry, monkeypatch):
    _comfy(registry, monkeypatch, running=True, files=True)
    state = model_availability(registry, "comfyui", WAN22_ID, Capability.IMAGE_TO_VIDEO)
    assert state.state is State.FREE and state.unattended
    chosen, _ = autopilot.best_ai_model(registry)
    assert chosen == ("comfyui", WAN22_ID)


def test_workflow_only_models_are_never_chosen_automatically(registry, monkeypatch):
    _comfy(registry, monkeypatch, running=True, files=False)
    state = model_availability(registry, "catalog", "minimax-h3", Capability.IMAGE_TO_VIDEO)
    assert state.usable and not state.unattended, "selectable by hand, not by Auto"
    chosen, _ = autopilot.best_ai_model(registry)
    assert chosen is None, "nothing here can run without a hand-made workflow file"


def test_missing_files_say_which_files(registry, monkeypatch):
    _comfy(registry, monkeypatch, running=True, files=False)
    state = model_availability(registry, "comfyui", WAN22_ID, Capability.IMAGE_TO_VIDEO)
    assert state.state is State.LOCKED
    assert "wan2.2_ti2v_5B" in state.reason and "not ready" not in state.reason


def test_comfyui_not_running_is_not_reported_as_a_missing_key(registry, monkeypatch):
    _comfy(registry, monkeypatch, running=False, files=True)
    state = model_availability(registry, "comfyui", WAN22_ID, Capability.IMAGE_TO_VIDEO)
    assert state.state is State.LOCKED and "not running" in state.reason


def test_catalogue_routes_wan_to_the_built_in_model(registry, monkeypatch):
    _comfy(registry, monkeypatch, running=True, files=True)
    provider, endpoint, _ = registry.get("catalog").resolve_route(
        "wan-2.2", Capability.IMAGE_TO_VIDEO)
    assert (provider.key, endpoint) == ("comfyui", WAN22_ID)


def test_catalogue_skips_a_workflow_route_it_cannot_run(registry, monkeypatch):
    _comfy(registry, monkeypatch, running=True, files=False)
    with pytest.raises(ProviderError) as error:
        registry.get("catalog").resolve_route("minimax-h3", Capability.IMAGE_TO_VIDEO)
    assert "workflow" in str(error.value)


def test_auto_spends_fewer_shots_on_local_generation():
    assert autopilot._ai_budget(10, "auto", local=True) == autopilot.LOCAL_AUTO_BUDGET
    assert autopilot._ai_budget(10, "auto", local=False) == 6
    assert autopilot._ai_budget(10, "always", local=True) == 10


# -- the graph ----------------------------------------------------------------------------
def test_frames_are_4k_plus_1_and_capped():
    for seconds in (0.5, 1.0, 2.0, 3.0, 9.0):
        frames = comfy_builtin.frames_for(seconds)
        assert (frames - 1) % 4 == 0 and 17 <= frames <= comfy_builtin.MAX_FRAMES


def test_sizes_follow_the_frame_and_the_vae_grid():
    for aspect in ((16, 9), (9, 16), (1, 1)):
        width, height = comfy_builtin.size_for(*aspect, "Standard")
        assert width % 32 == 0 and height % 32 == 0
        assert (width > height) == (aspect[0] > aspect[1])


def test_graph_uses_the_photo_only_for_image_to_video():
    files = comfy_builtin.WanFiles("unet.safetensors", "umt5.safetensors", "vae.safetensors")
    with_photo = comfy_builtin.wan22_graph(files, "p", "n", "photo.png", 960, 544, 81, 20,
                                           5.0, 1, "x", single_file=True)
    assert with_photo["8"]["inputs"]["start_image"] == ["7", 0]
    assert with_photo["11"]["class_type"] == "SaveWEBM"
    text_only = comfy_builtin.wan22_graph(files, "p", "n", None, 960, 544, 81, 20, 5.0, 1,
                                          "x", single_file=False)
    assert "7" not in text_only and "start_image" not in text_only["8"]["inputs"]
    assert text_only["11"]["class_type"] == "SaveImage"


# -- fitting a clip into the film ---------------------------------------------------------
@pytest.mark.parametrize("shot_seconds", [5.5, 7.0])
def test_a_short_static_clip_fills_its_shot_and_gains_a_camera_move(
        locator, plain_clip, tmp_path, shot_seconds):
    settings = PhotoFilmSettings(aspect="16:9 landscape", fps=24)
    shot = Shot(path=plain_clip, seconds=shot_seconds, move="Zoom in", ai_static_camera=True)
    output = tmp_path / "conformed.mp4"
    command = build_conform(locator, plain_clip, output, settings, shot, 0,
                            source_seconds=probe(locator, plain_clip).duration)
    result = subprocess.run(command.argv, capture_output=True, text=True,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert result.returncode == 0, result.stderr[-800:]
    info = probe(locator, output)
    assert (info.width, info.height) == settings.size
    assert abs(info.duration - shot_seconds) < 0.1, "the shot must keep its planned length"


# -- live ---------------------------------------------------------------------------------
@pytest.mark.skipif(os.environ.get("MEDIAFORGE_LIVE_COMFYUI") != "1",
                    reason="set MEDIAFORGE_LIVE_COMFYUI=1 to run against a real ComfyUI")
def test_live_wan_clip_from_a_photo(registry, photos, tmp_path, locator):
    from mediaforge.providers import GenerationRequest

    comfy = registry.get("comfyui")
    ready, why = comfy.builtin_status(WAN22_ID)
    if not ready:
        pytest.skip(why)
    steps = []
    result = comfy.generate(GenerationRequest(
        capability=Capability.IMAGE_TO_VIDEO, model_id=WAN22_ID,
        prompt="Gentle natural motion, the camera holds steady.",
        reference_images=[photos[0]], output_dir=tmp_path,
        options={"quality": "Draft", "seconds": 1.0, "aspect_ratio": "16:9"}),
        progress=lambda fraction, message: steps.append((fraction, message)))
    clip = Path(result.files[0])
    info = probe(locator, clip)
    assert info.duration > 0.6 and (info.width, info.height) == (640, 352)
    assert result.raw.get("camera") == "static"
    assert any("step" in message for _, message in steps), "progress came from the sampler"
