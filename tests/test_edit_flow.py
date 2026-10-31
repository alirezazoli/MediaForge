"""The Edit screen, driven the way a person drives it: file, tool, settings, run."""

from __future__ import annotations

from mediaforge.ffmpeg_tools import probe
from mediaforge.ui import edit_panel

from conftest import wait_until


def _load(window, clip):
    context = window.context
    asset = context.require_project().asset(context.catalogue(clip, source="import"))
    panel = window.edit_panel
    panel.set_asset(asset)
    window.go_to("edit")
    return panel


def test_every_tool_has_a_settings_page(window):
    panel = window.edit_panel
    assert set(panel._pages) == set(edit_panel.TOOLS)
    for key in edit_panel.TOOLS:
        panel.tiles.select(key)
        panel._on_operation_changed()
        assert panel.parameter_stack.currentWidget() is panel._pages[key]
        assert panel.tool_title.text() == edit_panel.TOOLS[key][0]


def test_next_is_blocked_until_a_file_is_chosen(window):
    panel = window.edit_panel
    panel.flow.go_to(edit_panel.STEP_FILE)
    assert not panel.flow.next_button.isEnabled()


def test_choosing_a_tile_opens_its_settings(window, plain_clip):
    panel = _load(window, plain_clip)
    panel.flow.go_to(edit_panel.STEP_TOOL)
    panel.tiles._tiles["grade"].click()
    assert panel.flow.current() == edit_panel.STEP_SETTINGS
    assert panel.tool_title.text() == "Colour"


def test_trim_runs_reports_in_place_and_chains(window, qapp, plain_clip, locator):
    panel = _load(window, plain_clip)
    panel.tiles.select("trim")
    panel._on_operation_changed()
    widgets = panel._pages["trim"].property("widgets")
    widgets["start"].setText("00:00:01.00")
    widgets["end"].setText("00:00:03.00")

    panel.run()
    assert panel.flow.current() == edit_panel.STEP_RESULT
    assert wait_until(qapp, lambda: panel.job_card.result_path is not None, 60), \
        panel.job_card.message.text()

    result = panel.job_card.result_path
    assert result.is_file()
    assert abs(probe(locator, result).duration - 2.0) < 0.25
    assert window.stack.currentWidget() is panel, "a finished edit must not jump screens"

    panel._continue_from_result()
    assert panel.asset.path == result
    assert panel.flow.current() == edit_panel.STEP_TOOL


def test_a_failed_edit_stays_on_the_edit_screen(window, qapp, plain_clip):
    panel = _load(window, plain_clip)
    panel.tiles.select("lut")
    panel._on_operation_changed()
    # No LUT chosen: refused before anything runs, and the user is sent to the setting.
    from unittest import mock
    with mock.patch.object(edit_panel.QMessageBox, "warning") as warned:
        panel.run()
    assert warned.called
    assert panel.flow.current() == edit_panel.STEP_SETTINGS
