"""The Generate screen with a stand-in provider, so no network or key is involved."""

from __future__ import annotations

from pathlib import Path

import pytest

from mediaforge.providers import Capability, GenerationResult, ModelSpec
from mediaforge.providers.base import Provider
from mediaforge.ui import generate_panel as gp

from conftest import wait_until


class StubProvider(Provider):
    key = "stub"
    name = "Stub"
    models = [ModelSpec("stub-image", "Stub image", [Capability.TEXT_TO_IMAGE])]

    def generate(self, request, progress=lambda f, m: None, should_cancel=None):
        from PIL import Image

        progress(0.5, "Painting")
        path = self._stamped_path(request.output_dir, "stub", ".png")
        Image.new("RGB", (64, 48), (200, 120, 40)).save(path)
        return GenerationResult(files=[path], provider=self.key, model_id=request.model_id)


@pytest.fixture()
def panel(window):
    window.context.registry._providers.append(StubProvider())
    panel = window.generate_panel
    panel._reload_capabilities()
    window.go_to("generate")
    return panel


def _choose(panel, capability: Capability, provider_key: str) -> None:
    panel.tiles.select(capability.value)
    panel._on_task_chosen(capability.value)
    panel.provider_box.setCurrentIndex(panel.provider_box.findData(provider_key))


def test_only_tasks_with_a_provider_are_offered(panel):
    offered = set(panel.tiles.keys())
    assert Capability.TEXT_TO_IMAGE.value in offered
    for key in offered:
        assert panel.context.registry.for_capability(Capability(key))


def test_choosing_a_task_moves_to_the_model_step(panel):
    _choose(panel, Capability.TEXT_TO_IMAGE, "stub")
    assert panel.flow.current() == gp.STEP_MODEL
    assert panel.model_box.currentData() == "stub-image"


def test_prompt_step_blocks_until_a_prompt_is_written(panel):
    _choose(panel, Capability.TEXT_TO_IMAGE, "stub")
    panel.flow.go_to(gp.STEP_PROMPT)
    panel.prompt_box.clear()
    assert not panel.flow.next_button.isEnabled()
    panel.prompt_box.setPlainText("a red barn at dusk")
    assert panel.flow.next_button.isEnabled()


def test_fields_follow_the_task(panel):
    _choose(panel, Capability.TEXT_TO_IMAGE, "stub")
    assert not panel.negative_box.isHidden()
    panel.tiles.select(Capability.TRANSCRIBE.value)
    panel._reload_providers()
    assert panel.negative_box.isHidden()
    assert not panel.reference_card.isHidden()


def test_generate_reports_on_the_result_step(panel, qapp, window):
    _choose(panel, Capability.TEXT_TO_IMAGE, "stub")
    panel.prompt_box.setPlainText("a red barn at dusk")
    panel.generate()
    assert panel.flow.current() == gp.STEP_RESULT
    assert wait_until(qapp, lambda: panel.result_list.count() == 1, 30)
    path = Path(panel.result_list.item(0).data(gp.Qt.ItemDataRole.UserRole))
    assert path.is_file()
    assert window.stack.currentWidget() is panel


def test_a_stopped_comfyui_offers_start_not_a_key(panel, monkeypatch):
    from mediaforge import comfyserver
    from mediaforge.providers.comfy_builtin import WAN22_ID

    comfy = panel.context.registry.get("comfyui")
    monkeypatch.setattr(type(comfy), "reachable", lambda self, force=False: False)
    monkeypatch.setattr(panel.context, "comfy_install",
                        lambda: comfyserver.ComfyInstall(Path("."), Path("python.exe"), False))
    _choose(panel, Capability.IMAGE_TO_VIDEO, "comfyui")
    assert panel.model_box.findData(WAN22_ID) >= 0
    assert not panel.key_button.isHidden()
    assert panel.key_button.text() == "Start ComfyUI"
