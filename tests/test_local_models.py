"""The models already on this PC: found, offered, chosen first, and run with the right settings.

All with stand-ins for ComfyUI and Ollama; tools/feature_tour.py runs them for real.
"""

from __future__ import annotations

import pytest

from mediaforge import availability
from mediaforge.config import Credentials, Settings
from mediaforge.providers import Capability, GenerationRequest, build_registry
from mediaforge.providers import comfy_builtin, comfy_local
from mediaforge.providers.ollama import OllamaModel, OllamaProvider

FILES = {
    comfy_local.SDXL_ID: {"checkpoint": "Juggernaut-XL_v9.safetensors"},
    comfy_local.ACE_STEP_ID: {"checkpoint": "ace_step_1.5_turbo_aio.safetensors"},
    comfy_local.ESRGAN_ID: {"upscaler": "RealESRGAN_x4plus.safetensors"},
    comfy_local.S2V_ID: {"unet_gguf": "Wan2.2-S2V-14B-Q4_K_M.gguf", "text_encoder": "umt5",
                         "vae": "wan_2.1_vae.safetensors", "audio_encoder": "wav2vec2"},
}


@pytest.fixture()
def registry(monkeypatch):
    built = build_registry(Credentials(), Settings())
    for provider in built:
        provider.api_key = ""
    comfy = built.get("comfyui")
    monkeypatch.setattr(type(comfy), "reachable", lambda self, force=False: True)
    monkeypatch.setattr(comfy_local, "find_files",
                        lambda base, model_id, force=False: FILES.get(model_id))
    monkeypatch.setattr(comfy_builtin, "find_wan22_files",
                        lambda base, force=False: comfy_builtin.WanFiles("u", "t", "v"))
    monkeypatch.setattr(OllamaProvider, "list_models", lambda self, force=False: [
        OllamaModel("qwen3.5:4b", 3_300_000_000, supports_tools=True, supports_vision=True),
        OllamaModel("llama3.1:8b", 4_900_000_000, supports_tools=True),
        OllamaModel("kimi-k3:cloud", 0, supports_tools=True, is_cloud=True),
    ])
    return built


def test_comfy_lists_files_in_both_object_info_formats(monkeypatch):
    """Newer ComfyUI nodes say "COMBO" and put the files under options; the lookup read
    the word COMBO as the list and found no upscaler and no S2V model."""
    info = {"UpscaleModelLoader": {"input": {"required": {"model_name": [
        "COMBO", {"options": ["RealESRGAN_x4plus.safetensors"]}]}}},
        "CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [
            ["Juggernaut-XL_v9.safetensors", "ace_step_1.5_turbo_aio.safetensors"]]}}}}
    monkeypatch.setattr(comfy_builtin, "request_json",
                        lambda method, url, **kw: {url.rsplit("/", 1)[1]:
                                                   info[url.rsplit("/", 1)[1]]})
    assert comfy_builtin._choices("x", "UpscaleModelLoader", "model_name") == \
        ["RealESRGAN_x4plus.safetensors"]
    assert "ace_step_1.5_turbo_aio.safetensors" in comfy_builtin._choices(
        "x", "CheckpointLoaderSimple", "ckpt_name")


@pytest.mark.parametrize("model_id,capability", [
    (comfy_local.SDXL_ID, Capability.TEXT_TO_IMAGE),
    (comfy_local.SDXL_ID, Capability.IMAGE_EDIT),
    (comfy_local.ACE_STEP_ID, Capability.TEXT_TO_MUSIC),
    (comfy_local.ESRGAN_ID, Capability.UPSCALE),
    (comfy_local.S2V_ID, Capability.LIP_SYNC),
])
def test_each_local_model_runs_without_a_key(registry, model_id, capability):
    state = availability.model_availability(registry, "comfyui", model_id, capability)
    assert state.state is availability.State.FREE and state.unattended, state.reason


def test_a_local_provider_is_offered_before_a_catalogue_that_needs_keys(registry):
    """The catalogue needs no key itself, so it read as ready and was picked first for
    stills -- and every model in it needed a key."""
    catalog, comfy = registry.get("catalog"), registry.get("comfyui")
    image = Capability.TEXT_TO_IMAGE
    assert availability._rank(availability.provider_availability_for(registry, comfy, image)) \
        <= availability._rank(availability.provider_availability_for(registry, catalog, image))


def test_ollama_offers_each_local_model_and_reads_video_with_the_seeing_ones(registry):
    ollama = registry.get("ollama")
    by_id = {spec.id: spec for spec in ollama.models}
    assert "kimi-k3:cloud" not in by_id, "cloud-routed models are not local"
    assert Capability.VIDEO_UNDERSTANDING in by_id["qwen3.5:4b"].capabilities
    assert Capability.VIDEO_UNDERSTANDING not in by_id["llama3.1:8b"].capabilities
    assert ollama.best_vision_model().name == "qwen3.5:4b"


def test_text_only_video_never_renders_below_standard(registry, monkeypatch, tmp_path):
    captured = {}

    def capture(files, prompt, negative, image_name, width, height, length, steps, *rest):
        captured.update(size=(width, height), steps=steps)
        raise RuntimeError("captured")

    monkeypatch.setattr(comfy_builtin, "wan22_graph", capture)
    monkeypatch.setattr(comfy_builtin, "has_node", lambda *args, **kwargs: True)
    comfy = registry.get("comfyui")
    with pytest.raises(RuntimeError, match="captured"):
        comfy.generate(GenerationRequest(Capability.TEXT_TO_VIDEO, comfy_builtin.WAN22_ID,
                                         prompt="waves at sunset",
                                         options={"quality": "Draft"}, output_dir=tmp_path))
    assert captured["size"] == comfy_builtin.SIZES["Standard"]
    assert captured["steps"] == comfy_builtin.STEPS["Standard"]


def test_generate_offers_music_when_ace_step_is_there(window, monkeypatch):
    monkeypatch.setattr(type(window.context.registry.get("comfyui")), "reachable",
                        lambda self, force=False: True)
    monkeypatch.setattr(comfy_local, "find_files",
                        lambda base, model_id, force=False: FILES.get(model_id))
    panel = window.generate_panel
    panel._reload_capabilities()
    assert Capability.TEXT_TO_MUSIC.value in panel.tiles.keys()


def test_sdxl_edit_starts_from_the_photo():
    graph = comfy_local.sdxl_graph(FILES[comfy_local.SDXL_ID], "dusk", "", 1344, 768, 30, 5.5,
                                   1, "p", image_name="room.png", strength=0.7)
    assert graph["4"]["class_type"] == "VAEEncode"
    assert graph["5"]["inputs"]["denoise"] == 0.7


def test_s2v_keeps_the_photos_shape_on_the_16_pixel_grid():
    width, height = comfy_local.s2v_size(1024, 1024, "Draft")
    assert width == height and width % 16 == 0
    width, height = comfy_local.s2v_size(1920, 1080, "Draft")
    assert width > height and width % 16 == 0 and height % 16 == 0
    assert (comfy_local.s2v_frames(2.0) - 1) % 4 == 0
