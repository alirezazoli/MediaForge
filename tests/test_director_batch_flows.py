"""Director gating, and a whole folder through Batch repair."""

from __future__ import annotations

import shutil
from unittest import mock

from mediaforge.ui import batch_panel as bp
from mediaforge.ui import director_panel as dp

from conftest import wait_until


def test_director_needs_a_brief_before_moving_on(window):
    panel = window.director_panel
    panel.flow.go_to(dp.STEP_BRIEF)
    panel.brief_box.clear()
    assert not panel.flow.next_button.isEnabled()
    panel.brief_box.setPlainText("A 10-second reel for a bakery.")
    assert panel.flow.next_button.isEnabled()


def test_director_refuses_without_a_writer(window):
    panel = window.director_panel
    panel.brief_box.setPlainText("A 10-second reel for a bakery.")
    panel.llm_model.clear()
    with mock.patch.object(dp.QMessageBox, "warning") as warned:
        panel.run(plan_only=True)
    assert warned.called
    assert panel.flow.current() == dp.STEP_CREW


def test_batch_measures_and_repairs_a_folder(window, qapp, faulty_clip, plain_clip, media_dir):
    folder = media_dir / "batch"
    folder.mkdir(exist_ok=True)
    shutil.copy(faulty_clip, folder / "a_faulty.mp4")
    shutil.copy(plain_clip, folder / "b_plain.mp4")

    panel = window.batch_panel
    window.go_to("batch")
    panel.folder = folder
    panel._reload_folder()
    assert [item.name for item in panel.items] == ["a_faulty.mp4", "b_plain.mp4"]

    panel.flow.go_to(1)
    assert not panel.flow.next_button.isEnabled(), "nothing measured yet"
    panel.scan()
    assert wait_until(qapp, lambda: all(i.report is not None for i in panel.items), 90)
    assert panel.flow.next_button.isEnabled()

    with mock.patch.object(bp.QMessageBox, "question",
                           return_value=bp.QMessageBox.StandardButton.Yes):
        panel.run()
    assert panel.flow.current() == 3
    assert panel.table_card.parent() is not None
    assert wait_until(qapp, lambda: "repaired" in panel.log.toPlainText().lower()
                      or "skipped" in panel.log.toPlainText().lower(), 180), \
        panel.log.toPlainText()
    done = [i for i in panel.items if i.state is bp.ItemState.DONE]
    assert any(i.name == "a_faulty.mp4" for i in done), panel.log.toPlainText()
    assert window.stack.currentWidget() is panel
