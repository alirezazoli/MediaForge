"""Batch panel: point it at a folder, see what is wrong with every file, fix them.

The table is the whole design. A batch tool that shows one progress bar and a count at
the end is asking to be trusted blindly across dozens of files; this one shows what was
measured in each, what it intends to do about it, and afterwards what actually happened —
including, per row, why a particular file failed while the rest went through.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton, QSpinBox, QTableWidget,
    QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from .. import batchrepair, gpu as gpu_module, repair
from ..batchrepair import BatchItem, ItemState
from ..context import AppContext
from .widgets import Card
from . import theme

COLUMNS = ["Do", "File", "What was measured", "What will happen"]

STATE_COLOURS = {
    ItemState.DONE: "SUCCESS",
    ItemState.FAILED: "DANGER",
    ItemState.SKIPPED: "TEXT_FAINT",
    ItemState.RUNNING: "ACCENT",
    ItemState.CANCELLED: "WARNING",
}


class BatchPanel(QWidget):
    status = Signal(str)
    library_changed = Signal()

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        self.folder: Path | None = None
        self.items: list[BatchItem] = []
        self._build()
        self._sync_controls()

    # -- construction --------------------------------------------------------------------
    def _build(self) -> None:
        layout = QVBoxLayout(self)

        banner = QLabel(
            "Repair a whole folder in one go. Every file is measured on its own, because "
            "noise and dead air differ from clip to clip. <b>Originals are never "
            "modified</b> — results are written to the project's renders folder."
        )
        banner.setWordWrap(True)
        layout.addWidget(banner)

        # -- folder ------------------------------------------------------------------
        source_card = Card("1 · Folder")
        self.folder_label = QLabel("No folder chosen.")
        self.folder_label.setWordWrap(True)
        source_card.body.addWidget(self.folder_label)
        row = QHBoxLayout()
        pick = QPushButton("Choose a folder…")
        pick.clicked.connect(self._pick_folder)
        self.recursive_box = QCheckBox("Include sub-folders")
        self.recursive_box.setChecked(True)
        self.recursive_box.toggled.connect(self._reload_folder)
        row.addWidget(pick)
        row.addWidget(self.recursive_box)
        row.addStretch(1)
        source_card.body.addLayout(row)
        layout.addWidget(source_card)

        # -- settings ----------------------------------------------------------------
        settings_card = Card("2 · Settings")
        form = QFormLayout()
        self.mode_box = QComboBox()
        self.mode_box.addItem("Decide per file from its own measurements", "auto")
        self.mode_box.addItem("Use the same settings for every file", "shared")
        self.mode_box.currentIndexChanged.connect(self._sync_controls)
        form.addRow("How to treat them", self.mode_box)

        self.silence_box = QSpinBox()
        self.silence_box.setRange(-90, -10)
        self.silence_box.setValue(repair.SILENCE_DB)
        self.silence_box.setSuffix(" dB")
        form.addRow("Silence below", self.silence_box)

        self.denoise_box = QComboBox()
        self.denoise_box.addItems([repair.DENOISE_NONE, repair.DENOISE_FAST,
                                   repair.DENOISE_BEST, repair.DENOISE_TEMPORAL])
        form.addRow("Reduce noise", self.denoise_box)
        self.denoise_strength = QDoubleSpinBox()
        self.denoise_strength.setRange(0.5, 20.0)
        self.denoise_strength.setValue(3.0)
        form.addRow("Noise strength", self.denoise_strength)
        self.cut_box = QCheckBox("Cut black and silent sections")
        self.cut_box.setChecked(True)
        form.addRow("", self.cut_box)
        self.upscale_box = QCheckBox("AI upscale every file")
        self.upscale_box.toggled.connect(self._sync_controls)
        form.addRow("", self.upscale_box)
        self.model_box = QComboBox()
        form.addRow("Trained for", self.model_box)
        self.scale_box = QComboBox()
        self.scale_box.addItems([f"x{value}" for value in repair.AI_SCALES])
        form.addRow("Scale", self.scale_box)
        settings_card.body.addLayout(form)

        self.settings_note = QLabel("")
        self.settings_note.setWordWrap(True)
        settings_card.body.addWidget(self.settings_note)
        layout.addWidget(settings_card)

        # -- table -------------------------------------------------------------------
        table_card = Card("3 · Files")
        button_row = QHBoxLayout()
        self.scan_button = QPushButton("Analyse every file")
        self.scan_button.setObjectName("primary")
        self.scan_button.clicked.connect(self.scan)
        self.run_button = QPushButton("Repair the ticked files")
        self.run_button.setObjectName("primary")
        self.run_button.clicked.connect(self.run)
        for label, value in (("Tick all", True), ("Untick all", False)):
            button = QPushButton(label)
            button.setObjectName("ghost")
            button.clicked.connect(lambda _=False, v=value: self._set_all(v))
            button_row.addWidget(button)
        button_row.addWidget(self.scan_button)
        button_row.addWidget(self.run_button)
        button_row.addStretch(1)
        self.tally = QLabel("")
        button_row.addWidget(self.tally)
        table_card.body.addLayout(button_row)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(lambda _item: self._update_tally())
        table_card.body.addWidget(self.table)
        layout.addWidget(table_card, 1)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(110)
        self.log.setPlaceholderText("Results appear here, one line per file.")
        layout.addWidget(self.log)

    # -- folder --------------------------------------------------------------------------
    def _pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder of videos")
        if folder:
            self.folder = Path(folder)
            self._reload_folder()

    def _reload_folder(self) -> None:
        if self.folder is None:
            return
        found = batchrepair.find_videos(self.folder, self.recursive_box.isChecked())
        self.items = [BatchItem(path) for path in found]
        self._fill_table()
        self.folder_label.setText(
            f"<b>{self.folder}</b><br>{len(found)} video file(s) found"
            + ("" if found else " — nothing here this tool can read.")
        )
        self.log.clear()
        self._sync_controls()

    # -- table ---------------------------------------------------------------------------
    def _fill_table(self) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.items))
        for row, item in enumerate(self.items):
            tick = QTableWidgetItem()
            tick.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            tick.setCheckState(Qt.CheckState.Checked)
            self.table.setItem(row, 0, tick)
            self.table.setItem(row, 1, QTableWidgetItem(item.name))
            self.table.setItem(row, 2, QTableWidgetItem(item.findings()))
            self.table.setItem(row, 3, QTableWidgetItem(item.summary()))
        self.table.blockSignals(False)
        self._update_tally()

    def update_row(self, item: BatchItem) -> None:
        """Called as each file finishes, so the table is a live account, not a summary."""
        try:
            row = self.items.index(item)
        except ValueError:
            return
        self.table.blockSignals(True)
        self.table.setItem(row, 2, QTableWidgetItem(item.findings()))
        cell = QTableWidgetItem(item.summary())
        token = STATE_COLOURS.get(item.state)
        if token:
            from PySide6.QtGui import QBrush, QColor
            cell.setForeground(QBrush(QColor(getattr(theme, token))))
        self.table.setItem(row, 3, cell)
        self.table.blockSignals(False)
        self.table.scrollToItem(cell)

    def _set_all(self, ticked: bool) -> None:
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item:
                item.setCheckState(Qt.CheckState.Checked if ticked
                                   else Qt.CheckState.Unchecked)
        self.table.blockSignals(False)
        self._update_tally()

    def ticked_items(self) -> list[BatchItem]:
        chosen = []
        for row, item in enumerate(self.items):
            cell = self.table.item(row, 0)
            if cell is not None and cell.checkState() == Qt.CheckState.Checked:
                chosen.append(item)
        return chosen

    def _update_tally(self) -> None:
        ticked = len(self.ticked_items())
        self.tally.setText(f"{ticked} of {len(self.items)} ticked")
        self._sync_controls()

    # -- settings -------------------------------------------------------------------------
    def _sync_controls(self) -> None:
        shared = self.mode_box.currentData() == "shared"
        for widget in (self.denoise_box, self.denoise_strength, self.cut_box,
                       self.upscale_box):
            widget.setEnabled(shared)
        wants_upscale = shared and self.upscale_box.isChecked()
        self.model_box.setEnabled(wants_upscale)
        self.scale_box.setEnabled(wants_upscale)

        scanned = [item for item in self.items if item.report is not None]
        self.scan_button.setEnabled(bool(self.items))
        self.run_button.setEnabled(bool(scanned) and bool(self.ticked_items()))

        tool_id = repair.installed_upscaler(self.context.local_tools)
        if self.model_box.count() == 0 and tool_id:
            tool = self.context.local_tools.get(tool_id)
            for entry in gpu_module.available_models(tool):
                speed = ("fastest" if entry.relative_cost <= 1.01
                         else f"{entry.relative_cost:g}x slower")
                self.model_box.addItem(f"{entry.label} — {speed}", entry.id)
            index = self.model_box.findData(gpu_module.DEFAULT_MODEL)
            self.model_box.setCurrentIndex(max(0, index))

        if not shared:
            self.settings_note.setText(
                "Each file gets the settings its own measurements call for. The AI "
                "upscale is never switched on automatically — it is the one step that "
                "costs minutes per file rather than seconds."
            )
        elif wants_upscale:
            frames, _ = batchrepair.estimate_batch(
                [i for i in self.ticked_items() if i.report], 1.0
            )
            self.settings_note.setText(
                f"<span style='color:{theme.WARNING}'>Every ticked file will be rebuilt "
                f"frame by frame on the GPU.</span> That is roughly {frames} frames "
                f"across this batch. Time one file on the Repair screen first if you "
                f"have not already — the cost per frame is the same there."
                if frames else
                "Analyse the files first so the amount of work can be counted."
            )
        else:
            self.settings_note.setText(
                "These settings replace each file's own recommendation. Cut points are "
                "still worked out per file — timestamps from one video mean nothing in "
                "another."
            )

    def shared_plan(self) -> repair.Plan:
        return repair.Plan(
            cut_dead=self.cut_box.isChecked(),
            denoise=self.denoise_box.currentText(),
            denoise_strength=self.denoise_strength.value(),
            sharpen=0.0,
            ai_upscale=self.upscale_box.isChecked(),
            ai_tool=repair.installed_upscaler(self.context.local_tools) or "realesrgan",
            ai_model=self.model_box.currentData() or gpu_module.DEFAULT_MODEL,
            ai_gpu=None,
            ai_scale=int(self.scale_box.currentText().lstrip("x") or 2),
        )

    # -- running ---------------------------------------------------------------------------
    def scan(self) -> None:
        if not self.items:
            QMessageBox.information(self, "No files", "Choose a folder with videos in it.")
            return
        if not self.context.locator.available:
            QMessageBox.warning(self, "ffmpeg required",
                                "Analysing videos needs ffmpeg. Install it in Settings.")
            return

        locator = self.context.locator
        paths = [item.path for item in self.items]
        silence_db = self.silence_box.value()
        tool_id = repair.installed_upscaler(self.context.local_tools)
        self.scan_button.setEnabled(False)
        self.log.setPlainText("Analysing…")

        def work(job_context):
            return batchrepair.scan_all(
                locator, paths, silence_db=silence_db,
                upscaler_installed=bool(tool_id), tool_id=tool_id or "realesrgan",
                progress=lambda fraction, message: job_context.progress(fraction, message),
                cancel=lambda: job_context.cancelled,
            )

        job = self.context.jobs.submit(f"Analyse {len(paths)} video(s)", work,
                                       category="batch_scan")
        self.status.emit(f"Job {job.id}: analysing {len(paths)} file(s)")

    def show_scan(self, outcome: batchrepair.BatchOutcome) -> None:
        self.scan_button.setEnabled(True)
        self.items = outcome.items
        self._fill_table()
        failed = [item for item in outcome.items if item.state is ItemState.FAILED]
        lines = [f"Analysed {len(outcome.items)} file(s)."]
        if failed:
            lines.append(f"{len(failed)} could not be read; they are marked in the table "
                         f"and will be left alone:")
            lines += [f"  · {item.name}: {item.error.splitlines()[0][:110]}"
                      for item in failed[:6]]
        nothing = [i for i in outcome.items
                   if i.plan is not None and not i.plan.does_anything]
        if nothing:
            lines.append(f"{len(nothing)} file(s) are already clean and will be skipped "
                         f"rather than re-encoded.")
        self.log.setPlainText("\n".join(lines))
        self._sync_controls()
        self.status.emit(f"Analysed {len(outcome.items)} file(s)")

    def scan_failed(self, error: str) -> None:
        self.scan_button.setEnabled(True)
        self.log.setPlainText(f"Could not analyse the folder: {error.splitlines()[0][:200]}")

    def run(self) -> None:
        if self.context.project is None:
            QMessageBox.information(self, "No project", "Create or open a project first.")
            return
        ticked = self.ticked_items()
        chosen = [item for item in ticked if item.report is not None]
        unreadable = [item for item in ticked
                      if item.report is None and item.state is ItemState.FAILED]
        if not chosen:
            QMessageBox.information(
                self, "Nothing to do",
                "None of the ticked files have been analysed yet. Press 'Analyse every "
                "file' first — the settings come from the measurements."
            )
            return

        if self.mode_box.currentData() == "shared":
            template = self.shared_plan()
            if template.ai_upscale:
                tool = self.context.local_tools.get(template.ai_tool)
                if tool is None or not tool.installed:
                    QMessageBox.warning(
                        self, "Upscaler not installed",
                        "The AI upscale is ticked but no upscaler is installed. Install "
                        "one on the Repair screen, or untick it."
                    )
                    return
            batchrepair.apply_shared_plan(chosen, template)

        working = [item for item in chosen if item.plan and item.plan.does_anything]
        skipping = len(chosen) - len(working)
        upscaling = [item for item in working if item.plan.ai_upscale]
        warning = ""
        if upscaling:
            frames, _ = batchrepair.estimate_batch(upscaling, 1.0)
            warning = (f"\n\n{len(upscaling)} file(s) will be rebuilt frame by frame on "
                       f"the GPU — about {frames} frames in total. This can run for a "
                       f"long time; the job can be cancelled from the Jobs screen and "
                       f"whatever finished is kept.")

        answer = QMessageBox.question(
            self, "Repair these files",
            f"{len(working)} file(s) will be repaired."
            + (f"\n{skipping} already clean and will be left untouched." if skipping else "")
            + (f"\n{len(unreadable)} could not be read at all and will be listed as "
               f"failures." if unreadable else "")
            + warning
            + "\n\nOriginals are not modified; results go to the project's renders folder."
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        project = self.context.require_project()
        destination = project.renders_dir
        locator = self.context.locator
        context = self.context
        # Files that failed to scan travel with the batch. No work is attempted on them --
        # they have no plan -- but they stay in the outcome, because otherwise the results
        # log reads "1 repaired, 1 skipped" and never mentions the file that broke.
        items = chosen + unreadable

        def work(job_context):
            return batchrepair.repair_all(
                locator, items, destination, local_tools=context.local_tools,
                progress=lambda fraction, message: job_context.progress(fraction, message),
                cancel=lambda: job_context.cancelled,
                on_output=lambda path, item: context.catalogue(
                    path, source="repair", label=f"{item.path.stem} repaired"),
            )

        job = self.context.jobs.submit(f"Repair {len(items)} video(s)", work,
                                       category="batch_repair")
        self.run_button.setEnabled(False)
        self.status.emit(f"Job {job.id}: repairing {len(items)} file(s)")

    def show_results(self, outcome: batchrepair.BatchOutcome) -> None:
        self.run_button.setEnabled(True)
        for item in outcome.items:
            self.update_row(item)
        lines = [outcome.sentence()]
        for item in outcome.items:
            if item.state is ItemState.FAILED:
                lines.append(f"  ✗ {item.name}: {item.error.splitlines()[0][:120]}")
            elif item.state is ItemState.SKIPPED:
                lines.append(f"  – {item.name}: {item.note}")
            elif item.state is ItemState.DONE and item.output:
                lines.append(f"  ✓ {item.name} → {item.output.name}")
        self.log.setPlainText("\n".join(lines))
        self.library_changed.emit()
        self.status.emit(outcome.sentence())
        self._sync_controls()

    def run_failed(self, error: str) -> None:
        self.run_button.setEnabled(True)
        self.log.setPlainText(f"The batch could not start: {error.splitlines()[0][:200]}")

    def refresh_status(self) -> None:
        self._sync_controls()
