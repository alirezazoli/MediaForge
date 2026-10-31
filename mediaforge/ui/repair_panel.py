"""Repair panel: analyse a video, then clean it up.

The order on screen is the order of the work: pick a file, look at what is actually wrong
with it, decide what to do about that, run it. Every setting arrives pre-filled from the
measurement and every one of them can be changed, because a measurement is evidence, not
a verdict -- grain the user put there on purpose is still grain to a noise estimator.

The dead sections get a row each with its own tick box. Deleting someone's footage on the
strength of a threshold, without showing them which seconds are going, is not something
this panel does.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton, QSpinBox, QTableWidget,
    QTableWidgetItem, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from .. import diagnose, ffmpeg_tools, gpu as gpu_module, localai, repair
from ..context import AppContext
from ..project import Asset
from .compare import ComparePanel
from .stepper import Step, StepFlow
from .widgets import Card, MediaPreview
from . import theme

DENOISE_CHOICES = [
    repair.DENOISE_NONE,
    repair.DENOISE_FAST,
    repair.DENOISE_BEST,
    repair.DENOISE_TEMPORAL,
]

UPSCALER_LABELS = {
    "realesrgan": "Real-ESRGAN (photos and film)",
    "realcugan": "Real-CUGAN (drawn and animated)",
    "waifu2x": "waifu2x (drawn, gentler)",
}


def _page(inner: QWidget) -> QWidget:
    """Same measure on every step: centred, and capped so a label and its control do not
    end up at opposite ends of a wide screen."""
    page = QWidget()
    layout = QHBoxLayout(page)
    layout.setContentsMargins(theme.SPACE_5, theme.SPACE_4, theme.SPACE_5, theme.SPACE_4)
    layout.addStretch(1)
    inner.setMaximumWidth(980)
    layout.addWidget(inner, 6)
    layout.addStretch(1)
    return page


class RepairPanel(QWidget):
    status = Signal(str)
    library_changed = Signal()

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        self.asset: Asset | None = None
        self.source: Path | None = None
        self.report: repair.Report | None = None
        self.plan: repair.Plan | None = None
        self._last_plan: repair.Plan | None = None
        self._build()
        self._sync_controls()

    # -- construction ----------------------------------------------------------------------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.flow = StepFlow([
            Step("source", "Video", "Pick the video to work on."),
            Step("analyse", "Analyse", "Measure what is actually wrong with it."),
            Step("fix", "Fix", "Choose what to do about each finding."),
            Step("result", "Result", "Run it, then compare before and after."),
        ], finish_label="Repair video")

        left = QWidget()
        layout = QVBoxLayout(left)

        self.banner = QLabel(
            "The noise level, the softness and the dead air are "
            "<b>measured from your file</b>, not guessed — and every setting below can be "
            "changed before anything is written. Your original is never modified."
        )
        self.banner.setWordWrap(True)
        layout.addWidget(self.banner)

        # -- source ------------------------------------------------------------------
        source_card = Card("Video to repair")
        self.source_label = QLabel("Nothing selected.")
        self.source_label.setWordWrap(True)
        source_card.body.addWidget(self.source_label)
        row = QHBoxLayout()
        pick = QPushButton("Choose a video…")
        pick.clicked.connect(self._pick_file)
        row.addWidget(pick)
        row.addStretch(1)
        source_card.body.addLayout(row)
        layout.addWidget(source_card)
        layout.addStretch(1)
        self.flow.add_page(_page(left))

        # ---- step 2: measure -------------------------------------------------
        analyse_page = QWidget()
        layout = QVBoxLayout(analyse_page)
        layout.setSpacing(theme.SPACE_3)

        # -- scan --------------------------------------------------------------------
        scan_card = Card("Analyse")
        form = QFormLayout()
        self.silence_box = QSpinBox()
        self.silence_box.setRange(-90, -10)
        self.silence_box.setValue(repair.SILENCE_DB)
        self.silence_box.setSuffix(" dB")
        self.silence_box.setToolTip(
            "Anything quieter than this counts as silence. -45 dB is room tone; raise it "
            "towards -30 if quiet passages are being missed."
        )
        form.addRow("Silence below", self.silence_box)

        self.mode_box = QComboBox()
        self.mode_box.addItems(list(repair.CUT_MODES))
        self.mode_box.setToolTip(
            "What counts as worth cutting. 'Black and silent' only removes stretches "
            "that are both, which is unambiguous rubbish. 'Silence only' suits a talking "
            "video, where the picture never goes dark — but it will also remove a "
            "deliberate pause."
        )
        self.mode_box.currentIndexChanged.connect(self._on_mode_changed)
        form.addRow("What to cut", self.mode_box)

        self.padding_box = QDoubleSpinBox()
        self.padding_box.setRange(0.0, 1.0)
        self.padding_box.setSingleStep(0.02)
        self.padding_box.setDecimals(2)
        self.padding_box.setValue(repair.SPEECH_PADDING)
        self.padding_box.setSuffix(" s")
        self.padding_box.setToolTip(
            "How much silence to keep either side of speech. Cutting flush to the "
            "detected boundary clips the start of the first word."
        )
        self.padding_box.valueChanged.connect(self._on_mode_changed)
        form.addRow("Keep around speech", self.padding_box)
        self.min_dead_box = QDoubleSpinBox()
        self.min_dead_box.setRange(0.1, 30.0)
        self.min_dead_box.setSingleStep(0.25)
        self.min_dead_box.setValue(repair.MIN_DEAD_SECONDS)
        self.min_dead_box.setSuffix(" s")
        self.min_dead_box.setToolTip("Shorter gaps than this are treated as edits, not dead air.")
        form.addRow("Shortest dead gap", self.min_dead_box)
        scan_card.body.addLayout(form)

        scan_row = QHBoxLayout()
        self.scan_button = QPushButton("Analyse this video")
        self.scan_button.setObjectName("primary")
        self.scan_button.clicked.connect(self.scan)
        scan_row.addWidget(self.scan_button)
        scan_row.addStretch(1)
        scan_card.body.addLayout(scan_row)

        self.findings = QLabel("Not analysed yet.")
        self.findings.setWordWrap(True)
        self.findings.setTextFormat(Qt.TextFormat.RichText)
        scan_card.body.addWidget(self.findings)
        layout.addWidget(scan_card)
        layout.addStretch(1)
        self.flow.add_page(_page(analyse_page))

        # ---- step 3: decide --------------------------------------------------
        fix_page = QWidget()
        layout = QVBoxLayout(fix_page)
        layout.setSpacing(theme.SPACE_3)

        # -- dead sections -----------------------------------------------------------
        self.dead_card = Card("Dead sections found")
        self.dead_hint = QLabel(
            "Untick anything you want to keep. Nothing is removed from the original file."
        )
        self.dead_hint.setWordWrap(True)
        self.dead_card.body.addWidget(self.dead_hint)
        self.dead_table = QTableWidget(0, 4)
        self.dead_table.setHorizontalHeaderLabels(["Cut", "From", "To", "Length"])
        self.dead_table.verticalHeader().setVisible(False)
        self.dead_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.dead_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.dead_table.setMaximumHeight(190)
        header = self.dead_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        for column in (1, 2, 3):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        self.dead_table.itemChanged.connect(self._on_cut_toggled)
        self.dead_card.body.addWidget(self.dead_table)

        tick_row = QHBoxLayout()
        for label, value in (("Cut all", True), ("Keep all", False)):
            button = QPushButton(label)
            button.setObjectName("ghost")
            button.clicked.connect(lambda _=False, v=value: self._set_all_cuts(v))
            tick_row.addWidget(button)
        tick_row.addStretch(1)
        self.dead_summary = QLabel("")
        tick_row.addWidget(self.dead_summary)
        self.dead_card.body.addLayout(tick_row)
        layout.addWidget(self.dead_card)

        # -- treatment ---------------------------------------------------------------
        plan_card = Card("What to fix")
        plan_form = QFormLayout()

        self.deinterlace_box = QCheckBox("Deinterlace (remove comb teeth)")
        plan_form.addRow("", self.deinterlace_box)

        self.crop_box = QCheckBox("Crop the black bars off")
        self.crop_box.setToolTip(
            "Removes letterbox or pillarbox bars, so the bitrate is spent on picture."
        )
        plan_form.addRow("", self.crop_box)

        self.stabilise_box = QCheckBox("Stabilise the camera")
        self.stabilise_box.setToolTip(
            "Two extra passes: one to measure the movement, one to smooth it. It crops "
            "in slightly to hide the edges the camera swung past."
        )
        self.stabilise_box.toggled.connect(self._sync_controls)
        plan_form.addRow("", self.stabilise_box)

        self.smoothing_box = QSpinBox()
        self.smoothing_box.setRange(1, 60)
        self.smoothing_box.setValue(10)
        self.smoothing_box.setToolTip(
            "Frames of camera path to average. Higher is smoother but crops in more and "
            "lags real movement."
        )
        plan_form.addRow("Stabiliser smoothing", self.smoothing_box)

        self.loudness_box = QCheckBox("Set the loudness")
        self.loudness_box.toggled.connect(self._sync_controls)
        plan_form.addRow("", self.loudness_box)

        self.loudness_target_box = QComboBox()
        for label in diagnose.LOUDNESS_TARGETS:
            self.loudness_target_box.addItem(label, diagnose.LOUDNESS_TARGETS[label])
        self.loudness_target_box.setCurrentText(diagnose.DEFAULT_LOUDNESS)
        plan_form.addRow("Loudness target", self.loudness_target_box)

        self.denoise_box = QComboBox()
        self.denoise_box.addItems(DENOISE_CHOICES)
        plan_form.addRow("Reduce noise", self.denoise_box)

        self.denoise_strength = QDoubleSpinBox()
        self.denoise_strength.setRange(0.5, 20.0)
        self.denoise_strength.setSingleStep(0.5)
        self.denoise_strength.setValue(3.0)
        plan_form.addRow("Noise strength", self.denoise_strength)

        self.deband_box = QCheckBox("Smooth banding in gradients")
        plan_form.addRow("", self.deband_box)

        self.sharpen_box = QDoubleSpinBox()
        self.sharpen_box.setRange(0.0, 3.0)
        self.sharpen_box.setSingleStep(0.1)
        plan_form.addRow("Sharpen", self.sharpen_box)

        self.upscale_box = QCheckBox("AI upscale (runs on this machine)")
        self.upscale_box.toggled.connect(self._sync_controls)
        plan_form.addRow("", self.upscale_box)

        self.upscaler_box = QComboBox()
        self.upscaler_box.currentIndexChanged.connect(self._on_upscaler_changed)
        plan_form.addRow("Upscaler", self.upscaler_box)

        self.model_box = QComboBox()
        self.model_box.currentIndexChanged.connect(self._on_model_changed)
        plan_form.addRow("Trained for", self.model_box)

        self.device_box = QComboBox()
        self.device_box.setToolTip(
            "Which GPU does the work. The engine's own default is device 0, which on a "
            "laptop is usually the integrated chip."
        )
        plan_form.addRow("Run on", self.device_box)

        self.scale_box = QComboBox()
        self.scale_box.addItems([f"x{value}" for value in repair.AI_SCALES])
        self.scale_box.currentIndexChanged.connect(self._on_model_changed)
        plan_form.addRow("Scale", self.scale_box)

        self.quality_box = QSpinBox()
        self.quality_box.setRange(12, 30)
        self.quality_box.setValue(18)
        self.quality_box.setToolTip("Lower is better quality and a bigger file. 18 is "
                                    "visually lossless for most footage.")
        plan_form.addRow("Output quality (CRF)", self.quality_box)
        plan_card.body.addLayout(plan_form)

        self.upscale_note = QLabel("")
        self.upscale_note.setWordWrap(True)
        plan_card.body.addWidget(self.upscale_note)

        estimate_row = QHBoxLayout()
        self.estimate_button = QPushButton("Time it on my machine")
        self.estimate_button.setObjectName("ghost")
        self.estimate_button.setToolTip(
            "Runs the real model on real frames from this video and measures how long a "
            "frame takes, so the total is a measurement rather than a guess."
        )
        self.estimate_button.clicked.connect(self.measure_time)
        estimate_row.addWidget(self.estimate_button)
        estimate_row.addStretch(1)
        plan_card.body.addLayout(estimate_row)
        self.estimate_label = QLabel("")
        self.estimate_label.setWordWrap(True)
        plan_card.body.addWidget(self.estimate_label)

        reset_row = QHBoxLayout()
        self.reset_button = QPushButton("Reset to the suggested settings")
        self.reset_button.setObjectName("ghost")
        self.reset_button.clicked.connect(self._apply_recommendation)
        reset_row.addWidget(self.reset_button)
        reset_row.addStretch(1)
        plan_card.body.addLayout(reset_row)
        layout.addWidget(plan_card)

        # -- reasoning ----------------------------------------------------------------
        why_card = Card("Why these settings")
        self.why = QTextEdit()
        self.why.setReadOnly(True)
        self.why.setMaximumHeight(140)
        self.why.setPlaceholderText("Analyse a video and the reasoning appears here.")
        why_card.body.addWidget(self.why)
        layout.addWidget(why_card)
        layout.addStretch(1)
        self.flow.add_page(_page(fix_page))

        run_row = QHBoxLayout()
        self.run_button = QPushButton("Repair video")
        self.run_button.setObjectName("primary")
        self.run_button.clicked.connect(self.run)
        self.install_button = QPushButton("Install the AI upscaler")
        self.install_button.clicked.connect(self._install_upscaler)
        run_row.addWidget(self.run_button)
        run_row.addWidget(self.install_button)
        run_row.addStretch(1)
        layout.addLayout(run_row)

        # ---- step 4: the result ------------------------------------------------
        right = QWidget()
        right_layout = QVBoxLayout(right)
        self.right_tabs = QTabWidget()

        preview_page = QWidget()
        preview_layout = QVBoxLayout(preview_page)
        preview_layout.setContentsMargins(0, 6, 0, 0)
        self.preview = MediaPreview()
        preview_layout.addWidget(self.preview, 1)
        self.right_tabs.addTab(preview_page, "Preview")

        self.compare = ComparePanel(self.context.locator)
        self.right_tabs.addTab(self.compare, "Before / after")
        right_layout.addWidget(self.right_tabs, 2)

        self.output_box = QTextEdit()
        self.output_box.setReadOnly(True)
        self.output_box.setPlaceholderText("The repaired file appears here.")
        right_layout.addWidget(self.output_box, 1)
        self.flow.add_page(_page(right))

        outer.addWidget(self.flow, 1)
        self.flow.step_changed.connect(self._step_changed)
        self.flow.finished.connect(self.run)
        self.flow.go_to(0)

    def _step_changed(self, index: int) -> None:
        """Each step says what it needs before it will let the user move on."""
        has_source = self.source is not None
        analysed = self.report is not None
        if index == 0:
            self.flow.set_status("Choose a video." if not has_source
                                 else f"{self.source.name}")
            self.flow.set_next_enabled(has_source)
        elif index == 1:
            self.flow.set_status("Press Analyse — the settings come from the measurements."
                                 if not analysed else "Measured.")
            self.flow.set_next_enabled(analysed)
        elif index == 2:
            self.flow.set_status("Everything here can be overridden.")
            self.flow.set_next_enabled(analysed)
        else:
            self.flow.set_status("")
            self.flow.set_next_enabled(analysed)

    # -- source -----------------------------------------------------------------------------
    def set_asset(self, asset: Asset) -> None:
        self.asset = asset
        self.set_source(asset.path)

    def set_source(self, path: Path) -> None:
        self.source = Path(path)
        self.report = None
        self.plan = None
        self.dead_table.setRowCount(0)
        self.findings.setText("Not analysed yet.")
        self.why.clear()
        self.source_label.setText(
            f"<b>{self.source.name}</b><br>"
            f"<span style='color:{theme.TEXT_FAINT}'>{self.source}</span>"
        )
        self.preview.show_path(self.source)
        if hasattr(self, 'flow'):
            self.flow._sync()
        # A different source makes any comparison on screen a comparison of two
        # unrelated files, so it goes rather than lingering.
        self.compare.clear()
        self._sync_controls()

    def _pick_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a video", "", "Video (*.mp4 *.mov *.mkv *.avi *.webm *.m4v);;All (*.*)"
        )
        if path:
            self.set_source(Path(path))

    # -- scanning ----------------------------------------------------------------------------
    def scan(self) -> None:
        if self.source is None or not self.source.exists():
            QMessageBox.information(self, "No video", "Choose a video to repair first.")
            return
        if not self.context.locator.available:
            QMessageBox.warning(self, "ffmpeg required",
                                "Analysing a video needs ffmpeg. Install it in Settings.")
            return

        locator = self.context.locator
        source = self.source
        silence_db = self.silence_box.value()
        min_dead = self.min_dead_box.value()
        mode = self.mode_box.currentText()
        padding = self.padding_box.value()
        target = float(self.loudness_target_box.currentData() or -14.0)
        self.scan_button.setEnabled(False)
        self.findings.setText("Analysing…")

        def work(job_context):
            return repair.scan(
                locator, source, silence_db=silence_db, min_dead=min_dead,
                mode=mode, padding=padding, loudness_target=target,
                progress=lambda fraction, message: job_context.progress(fraction, message),
            )

        job = self.context.jobs.submit(f"Analyse {source.name}", work, category="repair_scan")
        self.status.emit(f"Job {job.id}: analysing {source.name}")

    def show_report(self, report: repair.Report) -> None:
        self.scan_button.setEnabled(True)
        # The findings are what step three is for; land the user there rather than
        # leaving them on the button they already pressed.
        if hasattr(self, 'flow'):
            self.flow.go_to(2)
        self.report = report
        self.mode_box.blockSignals(True)
        self.mode_box.setCurrentText(report.mode)
        self.mode_box.blockSignals(False)
        lines = "".join(f"<li>{line}</li>" for line in report.findings())
        self.findings.setText(f"<ul style='margin-left:-22px'>{lines}</ul>")
        self._fill_dead_table(report.dead)
        self._apply_recommendation()
        self.status.emit(
            f"{len(report.dead)} dead section(s), noise {report.measurement.noise_word}"
        )

    def scan_failed(self, error: str) -> None:
        self.scan_button.setEnabled(True)
        self.findings.setText(
            f"<span style='color:{theme.DANGER}'>Could not analyse this file:</span> "
            f"{error.splitlines()[0][:200]}"
        )

    def _on_mode_changed(self) -> None:
        """Switching the rule re-derives the cuts from the scan already in hand.

        The black and silent spans were both measured in the original pass, so changing
        between them is arithmetic. Re-decoding the file to answer a dropdown would take
        as long as the first analysis and learn nothing new.
        """
        if self.report is None:
            return
        self.report.recompute(self.mode_box.currentText(), self.padding_box.value())
        lines = "".join(f"<li>{line}</li>" for line in self.report.findings())
        self.findings.setText(f"<ul style='margin-left:-22px'>{lines}</ul>")
        self._fill_dead_table(self.report.dead)
        self._apply_recommendation()
        self.status.emit(
            f"{len(self.report.dead)} section(s), {self.report.dead_seconds:.1f}s "
            f"under '{self.mode_box.currentText()}'"
        )

    def _fill_dead_table(self, dead: list[repair.Segment]) -> None:
        self.dead_table.blockSignals(True)
        self.dead_table.setRowCount(len(dead))
        for row, segment in enumerate(dead):
            tick = QTableWidgetItem()
            tick.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            tick.setCheckState(Qt.CheckState.Checked)
            self.dead_table.setItem(row, 0, tick)
            for column, text in enumerate((
                ffmpeg_tools.format_timecode(segment.start),
                ffmpeg_tools.format_timecode(segment.end),
                f"{segment.length:.1f}s",
            ), start=1):
                self.dead_table.setItem(row, column, QTableWidgetItem(text))
        self.dead_table.blockSignals(False)
        self._update_dead_summary()

    def _set_all_cuts(self, cut: bool) -> None:
        self.dead_table.blockSignals(True)
        for row in range(self.dead_table.rowCount()):
            item = self.dead_table.item(row, 0)
            if item:
                item.setCheckState(Qt.CheckState.Checked if cut else Qt.CheckState.Unchecked)
        self.dead_table.blockSignals(False)
        self._update_dead_summary()

    def _on_cut_toggled(self, _item) -> None:
        self._update_dead_summary()

    def selected_cuts(self) -> list[repair.Segment]:
        if self.report is None:
            return []
        chosen: list[repair.Segment] = []
        for row, segment in enumerate(self.report.dead):
            item = self.dead_table.item(row, 0)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                chosen.append(segment)
        return chosen

    def _update_dead_summary(self) -> None:
        cuts = self.selected_cuts()
        total = sum(segment.length for segment in cuts)
        if not self.report or not self.report.dead:
            self.dead_summary.setText("")
            return
        self.dead_summary.setText(
            f"Cutting {len(cuts)} of {len(self.report.dead)} · {total:.1f}s removed"
        )

    # -- plan ---------------------------------------------------------------------------------
    def _apply_recommendation(self) -> None:
        if self.report is None:
            return
        tool_id = repair.installed_upscaler(self.context.local_tools)
        plan = repair.recommend(self.report, upscaler_installed=bool(tool_id),
                                tool_id=tool_id or "realesrgan")
        self.plan = plan

        index = self.denoise_box.findText(plan.denoise)
        self.denoise_box.setCurrentIndex(max(0, index))
        self.denoise_strength.setValue(plan.denoise_strength)
        self.deinterlace_box.setChecked(plan.deinterlace)
        self.crop_box.setChecked(plan.crop is not None and not plan.crop.is_full_frame)
        self.crop_box.setEnabled(self.report is not None
                                 and not self.report.crop.is_full_frame)
        self.crop_box.setText(
            f"Crop the black bars off → {self.report.crop.width}x{self.report.crop.height}"
            if self.report is not None and not self.report.crop.is_full_frame
            else "Crop the black bars off (none found)"
        )
        self.stabilise_box.setChecked(plan.stabilise)
        self.smoothing_box.setValue(plan.stabilise_smoothing)
        self.loudness_box.setChecked(plan.normalise_audio)
        self.loudness_box.setEnabled(self.report is not None and self.report.audio.present)
        self.deband_box.setChecked(plan.deband)
        self.sharpen_box.setValue(plan.sharpen)
        self.upscale_box.setChecked(plan.ai_upscale)
        # A suggestion the user has not accepted still has to be visible, or "leave it off
        # by default" quietly becomes "never mention it".
        self.upscale_box.setText(
            "AI upscale — recommended for this video"
            if plan.ai_suggested and not plan.ai_upscale
            else "AI upscale (runs on this machine)"
        )
        self.quality_box.setValue(plan.crf)
        self.scale_box.blockSignals(True)
        scale_index = self.scale_box.findText(f"x{plan.ai_scale}")
        if scale_index >= 0:
            self.scale_box.setCurrentIndex(scale_index)
        self.scale_box.blockSignals(False)
        self._refresh_upscalers(plan.ai_tool)
        self.estimate_label.setText("")
        self._set_all_cuts(plan.cut_dead)

        self.why.setPlainText("\n".join(f"· {reason}" for reason in plan.reasons))
        self._sync_controls()

    def current_plan(self) -> repair.Plan:
        """What the controls say right now -- the recommendation only seeds them."""
        cuts = self.selected_cuts()
        return repair.Plan(
            cut_dead=bool(cuts),
            cuts=cuts,
            deinterlace=self.deinterlace_box.isChecked(),
            crop=(self.report.crop if self.crop_box.isChecked() and self.report else None),
            stabilise=self.stabilise_box.isChecked(),
            stabilise_smoothing=self.smoothing_box.value(),
            normalise_audio=self.loudness_box.isChecked(),
            loudness_target=float(self.loudness_target_box.currentData() or -14.0),
            denoise=self.denoise_box.currentText(),
            denoise_strength=self.denoise_strength.value(),
            deband=self.deband_box.isChecked(),
            sharpen=self.sharpen_box.value(),
            ai_upscale=self.upscale_box.isChecked(),
            ai_tool=self.upscaler_box.currentData() or "realesrgan",
            ai_model=self.model_box.currentData() or "",
            ai_gpu=self.device_box.currentData(),
            ai_scale=int(self.scale_box.currentText().lstrip("x") or 2),
            crf=self.quality_box.value(),
            reasons=list(self.plan.reasons) if self.plan else [],
        )

    def _refresh_upscalers(self, prefer: str = "") -> None:
        self.upscaler_box.clear()
        installed_any = False
        for tool_id in repair.UPSCALE_TOOLS:
            tool = self.context.local_tools.get(tool_id)
            if tool is None:
                continue
            ready = tool.installed
            installed_any = installed_any or ready
            label = UPSCALER_LABELS.get(tool_id, tool.name)
            # Not installed is not the same as unavailable: it is one download away, so the
            # entry stays selectable and says what is missing rather than disappearing.
            self.upscaler_box.addItem(
                label if ready else f"{label} — not installed (~{tool.size_mb} MB)", tool_id
            )
        if prefer:
            index = self.upscaler_box.findData(prefer)
            if index >= 0:
                self.upscaler_box.setCurrentIndex(index)
        self.any_upscaler_installed = installed_any
        self._refresh_models()
        self._refresh_devices()

    def _refresh_models(self) -> None:
        """Only models whose weights are on disk, with the cost of each said out loud."""
        tool_id = self.upscaler_box.currentData()
        tool = self.context.local_tools.get(tool_id) if tool_id else None
        wanted = self.model_box.currentData()
        self.model_box.blockSignals(True)
        self.model_box.clear()
        entries = gpu_module.available_models(tool) if tool and tool.installed else ()
        for entry in entries:
            speed = ("fastest" if entry.relative_cost <= 1.01
                     else f"{entry.relative_cost:g}x slower")
            self.model_box.addItem(f"{entry.label} — {speed}", entry.id)
        self.model_box.setEnabled(bool(entries))
        if not entries:
            self.model_box.addItem("(install the engine to choose)", "")
        index = self.model_box.findData(wanted or gpu_module.DEFAULT_MODEL)
        self.model_box.setCurrentIndex(max(0, index))
        self.model_box.blockSignals(False)

    def _refresh_devices(self) -> None:
        tool_id = self.upscaler_box.currentData()
        tool = self.context.local_tools.get(tool_id) if tool_id else None
        wanted = self.device_box.currentData()
        self.device_box.blockSignals(True)
        self.device_box.clear()
        found = gpu_module.devices(tool) if tool and tool.installed else []
        best = gpu_module.best_device(tool) if found else None
        for device in found:
            suffix = "  (recommended)" if best and device.index == best.index else ""
            self.device_box.addItem(device.label() + suffix, device.index)
        self.device_box.setEnabled(bool(found))
        if not found:
            self.device_box.addItem("(install the engine to choose)", None)
        index = self.device_box.findData(wanted if wanted is not None else
                                         (best.index if best else None))
        self.device_box.setCurrentIndex(max(0, index))
        self.device_box.blockSignals(False)

    def _on_upscaler_changed(self) -> None:
        self._refresh_models()
        self._refresh_devices()
        self._sync_controls()

    def _on_model_changed(self) -> None:
        """The measured estimate belongs to one model and scale; it does not survive a
        change of either, and showing a stale number is worse than showing none."""
        self.estimate_label.setText("")
        self._sync_controls()

    def _sync_controls(self) -> None:
        self.padding_box.setEnabled(self.mode_box.currentText() == repair.MODE_SILENCE)
        self.smoothing_box.setEnabled(self.stabilise_box.isChecked())
        self.loudness_target_box.setEnabled(self.loudness_box.isChecked())
        wants_upscale = self.upscale_box.isChecked()
        for widget in (self.upscaler_box, self.model_box, self.device_box, self.scale_box):
            widget.setEnabled(wants_upscale and widget.count() > 0)
        self.run_button.setEnabled(self.report is not None)
        self.dead_card.setVisible(self.report is None or bool(self.report.dead))

        tool_id = self.upscaler_box.currentData()
        tool = self.context.local_tools.get(tool_id) if tool_id else None
        installed = tool is not None and tool.installed
        self.install_button.setVisible(tool is not None and not installed)
        self.estimate_button.setEnabled(
            wants_upscale and installed and self.report is not None
        )

        if not wants_upscale or tool is None:
            self.upscale_note.setText("")
            return
        if not installed:
            self.upscale_note.setText(
                f"<span style='color:{theme.WARNING}'>{tool.name} is not installed "
                f"(~{tool.size_mb} MB, {tool.license}).</span> Press 'Install the AI "
                f"upscaler'. It is a one-off download and then it works offline forever."
            )
            return

        # An upscale is per-frame GPU work and the model choice swings it by an order of
        # magnitude. Saying "this may take a while" when the difference between two
        # entries in the dropdown is minutes versus hours is not good enough.
        entry = gpu_module.model(self.model_box.currentData() or "")
        note = f"<span style='color:{theme.FREE}'>{tool.name} is installed.</span>"
        if entry:
            note += f" <b>{entry.label}</b> suits {entry.suits}."
            if entry.relative_cost > 2:
                note += (f" It costs about {entry.relative_cost:g}x as much time per frame "
                         f"as the fast model.")
        note += (" Every frame is rebuilt, so press 'Time it on my machine' before you "
                 "commit to it.")
        self.upscale_note.setText(note)

    def measure_time(self) -> None:
        """Times the real model on real frames from this video."""
        if self.report is None or self.source is None:
            return
        plan = self.current_plan()
        tool = self.context.local_tools.get(plan.ai_tool)
        if tool is None or not tool.installed:
            QMessageBox.information(self, "Not installed",
                                    "Install the upscaler before timing it.")
            return

        frames = self.report.frames_after_cuts(plan.cuts if plan.cut_dead else [])
        locator = self.context.locator
        source = self.source
        report = self.report
        self.estimate_button.setEnabled(False)
        self.estimate_label.setText("Measuring…")

        def work(job_context):
            # A frame from the middle of the surviving footage, at native resolution --
            # cost scales with pixel count, so a stand-in of the wrong size lies.
            sample = Path(tempfile.gettempdir()) / f"mf_bench_{source.stem}.png"
            at = repair.sample_times(report.info.duration,
                                     plan.cuts if plan.cut_dead else [], 1)[0]
            grab = subprocess.run(
                [locator.ffmpeg, "-y", "-v", "error", "-ss", f"{at:.3f}", "-i", str(source),
                 "-frames:v", "1", str(sample)],
                capture_output=True, creationflags=repair._NO_WINDOW,
            )
            if grab.returncode != 0 or not sample.exists():
                raise RuntimeError("Could not take a sample frame from this video.")
            try:
                return gpu_module.estimate(
                    tool, sample, frames=frames, scale=plan.ai_scale,
                    model_id=plan.ai_model or gpu_module.DEFAULT_MODEL,
                    device=gpu_module.device_by_index(tool, plan.ai_gpu),
                    progress=lambda fraction, message: job_context.progress(fraction, message),
                )
            finally:
                sample.unlink(missing_ok=True)

        job = self.context.jobs.submit(f"Time the upscale of {source.name}", work,
                                       category="repair_estimate")
        self.status.emit(f"Job {job.id}: timing the upscale")

    def show_estimate(self, estimate) -> None:
        self.estimate_button.setEnabled(True)
        colour = theme.WARNING if estimate.total_seconds > 1800 else theme.TEXT
        self.estimate_label.setText(
            f"<span style='color:{colour}'>{estimate.sentence()}</span>"
        )
        self.status.emit(estimate.sentence())

    def estimate_failed(self, error: str) -> None:
        self.estimate_button.setEnabled(True)
        self.estimate_label.setText(
            f"<span style='color:{theme.DANGER}'>Could not time it:</span> "
            f"{error.splitlines()[0][:200]}"
        )

    def _install_upscaler(self) -> None:
        tool_id = self.upscaler_box.currentData()
        tool = self.context.local_tools.get(tool_id) if tool_id else None
        if tool is None:
            QMessageBox.information(self, "No engine", "No upscaler is in the catalogue.")
            return
        if tool.installed:
            QMessageBox.information(self, "Already installed", f"{tool.name} is ready.")
            return
        answer = QMessageBox.question(
            self, "Install the upscaler",
            f"Download and install {tool.name}?\n\n"
            f"Source: {tool.repo}\nLicence: {tool.license}\n"
            f"Size: about {tool.size_mb} MB\n\n"
            "It runs on your own GPU afterwards, offline.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        def work(job_context):
            return localai.install_tool(
                tool, progress=lambda fraction, message: job_context.progress(fraction, message)
            )

        job = self.context.jobs.submit(f"Install {tool.name}", work, category="install")
        self.status.emit(f"Job {job.id}: installing {tool.name}")

    # -- running --------------------------------------------------------------------------------
    def run(self) -> None:
        if self.context.project is None:
            QMessageBox.information(self, "No project", "Create or open a project first.")
            return
        if self.source is None or self.report is None:
            QMessageBox.information(self, "Not analysed",
                                    "Analyse the video first so the settings mean something.")
            return

        plan = self.current_plan()
        if not plan.does_anything:
            QMessageBox.information(
                self, "Nothing to do",
                "Nothing is ticked, so a repair would just re-encode the file and lose a "
                "little quality for no reason."
            )
            return
        if plan.ai_upscale:
            tool = self.context.local_tools.get(plan.ai_tool)
            if tool is None or not tool.installed:
                QMessageBox.warning(
                    self, "Upscaler not installed",
                    f"The AI upscale is ticked but {plan.ai_tool} is not installed.\n\n"
                    "Install it first, or untick the AI upscale and just clean the file up."
                )
                return

        remaining = self.report.info.duration - sum(s.length for s in plan.cuts)
        answer = QMessageBox.question(
            self, "Repair this video",
            f"{self.source.name}\n\n"
            + "\n".join(f"• {step}" for step in plan.describe())
            + f"\n\nLength: {ffmpeg_tools.format_timecode(self.report.info.duration)}"
              f" → {ffmpeg_tools.format_timecode(remaining)}\n"
              f"The original file is not modified; the result is written to renders/."
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        project = self.context.require_project()
        destination = project.renders_dir / f"{self.source.stem}_repaired.mp4"
        counter = 1
        while destination.exists():
            destination = project.renders_dir / f"{self.source.stem}_repaired_{counter}.mp4"
            counter += 1

        context = self.context
        locator = self.context.locator
        source = self.source
        info = self.report.info
        # The comparison maps times through the cuts that actually ran, not
        # whatever the tick boxes happen to say once the job finishes.
        self._last_plan = plan

        def work(job_context):
            path = repair.repair(
                locator, source, destination, plan, info=info,
                local_tools=context.local_tools,
                progress=lambda fraction, message: job_context.progress(fraction, message),
                cancel=lambda: job_context.cancelled,
            )
            context.catalogue(path, source="repair", label=f"{source.stem} repaired")
            return path

        if hasattr(self, 'flow'):
            self.flow.go_to(len(self.flow.steps) - 1)
        job = self.context.jobs.submit(f"Repair {source.name}", work, category="repair")
        self.status.emit(f"Job {job.id}: repairing {source.name}")

    def show_result(self, result) -> None:
        if isinstance(result, Path):
            self.preview.show_path(result)
            summary = []
            if self.report:
                try:
                    after = ffmpeg_tools.probe(self.context.locator, result)
                    summary.append(
                        f"Length {ffmpeg_tools.format_timecode(self.report.info.duration)}"
                        f" → {ffmpeg_tools.format_timecode(after.duration)}"
                    )
                    summary.append(f"Resolution {self.report.info.resolution}"
                                   f" → {after.resolution}")
                except Exception:
                    pass
                self._show_comparison(result)
            self.output_box.setPlainText("\n".join([str(result), ""] + summary))
        self.library_changed.emit()
        self._sync_controls()

    def _show_comparison(self, result: Path) -> None:
        if hasattr(self, 'flow'):
            self.flow.go_to(len(self.flow.steps) - 1)
        """Puts the original and the result under one wipe, and brings it forward."""
        plan = self._last_plan or self.current_plan()
        self.compare.set_pair(
            self.source, result,
            plan.cuts if plan.cut_dead else [],
            self.report.info.duration,
            note=("Drag the line to wipe between them, and the Moment slider to move "
                  "through the video. Times are mapped through the cuts, so both sides "
                  "always show the same moment of the footage."),
        )
        self.right_tabs.setCurrentWidget(self.compare)

    def refresh_status(self) -> None:
        """Called after a theme switch or an engine install."""
        self._refresh_upscalers(self.upscaler_box.currentData() or "")
        self._sync_controls()
