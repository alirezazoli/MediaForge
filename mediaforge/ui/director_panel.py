"""Director panel: brief in, finished cut out."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QScrollArea, QSpinBox, QSplitter, QTextEdit,
    QVBoxLayout, QWidget,
)

from ..agents import Director, DirectorConfig, DirectorResult
from ..context import AppContext
from ..providers import Capability
from .widgets import ResponsiveSplitter, Card, MediaPreview


class DirectorPanel(QWidget):
    status = Signal(str)
    library_changed = Signal()

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        self._identity_references: list[Path] = []
        self._build()
        self.reload_providers()

    def _build(self) -> None:
        outer = QHBoxLayout(self)
        splitter = ResponsiveSplitter()

        left = QWidget()
        layout = QVBoxLayout(left)

        brief_card = Card("Brief")
        self.brief_box = QTextEdit()
        self.brief_box.setPlaceholderText(
            "What is the video for? Who is it aimed at? What should happen, and what should "
            "the viewer feel or do at the end?\n\n"
            "Example: A 20-second Instagram Reel for a specialty coffee roaster in Tehran. "
            "Warm morning light, hands pouring, steam, close macro of beans. Ends on the bag "
            "with the logo. Tone: calm, premium, no dialogue."
        )
        self.brief_box.setMinimumHeight(140)
        brief_card.body.addWidget(self.brief_box)

        self.style_box = QLineEdit("cinematic, natural light, shallow depth of field, 35mm")
        brief_card.body.addWidget(QLabel("Visual style (repeated into every shot prompt)"))
        brief_card.body.addWidget(self.style_box)

        shape_row = QHBoxLayout()
        self.shot_count = QSpinBox()
        self.shot_count.setRange(1, 20)
        self.shot_count.setValue(4)
        self.total_seconds = QSpinBox()
        self.total_seconds.setRange(4, 300)
        self.total_seconds.setValue(20)
        self.aspect_box = QComboBox()
        self.aspect_box.addItems(["16:9", "9:16", "1:1"])
        for label, widget in (("Shots", self.shot_count), ("Total seconds", self.total_seconds),
                              ("Aspect", self.aspect_box)):
            shape_row.addWidget(QLabel(label))
            shape_row.addWidget(widget)
        shape_row.addStretch(1)
        brief_card.body.addLayout(shape_row)
        layout.addWidget(brief_card)

        models_card = Card("Models")
        form = QFormLayout()
        self.llm_provider, self.llm_model = self._provider_model_pair(Capability.LLM)
        self.image_provider, self.image_model = self._provider_model_pair(Capability.TEXT_TO_IMAGE)
        self.video_provider, self.video_model = self._provider_model_pair(Capability.TEXT_TO_VIDEO)
        self.tts_provider, self.tts_model = self._provider_model_pair(Capability.TEXT_TO_SPEECH)

        form.addRow("Writer", _pair_row(self.llm_provider, self.llm_model))
        self.stills_check = QCheckBox("Generate a still per shot first (locks composition)")
        self.stills_check.setChecked(True)
        form.addRow(self.stills_check)
        form.addRow("Image model", _pair_row(self.image_provider, self.image_model))
        self.clips_check = QCheckBox("Generate video per shot")
        self.clips_check.setChecked(True)
        form.addRow(self.clips_check)
        form.addRow("Video model", _pair_row(self.video_provider, self.video_model))
        self.narration_check = QCheckBox("Generate narration and mix it under the cut")
        form.addRow(self.narration_check)
        form.addRow("Voice model", _pair_row(self.tts_provider, self.tts_model))
        self.voice_id_box = QLineEdit("21m00Tcm4TlvDq8ikWAM")
        form.addRow("Voice id", self.voice_id_box)
        models_card.body.addLayout(form)
        layout.addWidget(models_card)

        identity_card = Card("Identity references (optional)")
        identity_card.body.addWidget(QLabel(
            "Photos of a person, product or logo that must stay consistent across every shot."
        ))
        identity_row = QHBoxLayout()
        add_button = QPushButton("Add reference images")
        add_button.clicked.connect(self._add_references)
        clear_button = QPushButton("Clear")
        clear_button.clicked.connect(self._clear_references)
        identity_row.addWidget(add_button)
        identity_row.addWidget(clear_button)
        identity_row.addStretch(1)
        identity_card.body.addLayout(identity_row)
        self.identity_label = QLabel("None")
        self.identity_label.setWordWrap(True)
        identity_card.body.addWidget(self.identity_label)
        layout.addWidget(identity_card)

        assembly_card = Card("Assembly")
        assembly_row = QHBoxLayout()
        self.assemble_check = QCheckBox("Join shots into a final cut")
        self.assemble_check.setChecked(True)
        self.crossfade_box = QDoubleSpinBox()
        self.crossfade_box.setRange(0.0, 3.0)
        self.crossfade_box.setSingleStep(0.1)
        self.crossfade_box.setValue(0.5)
        assembly_row.addWidget(self.assemble_check)
        assembly_row.addWidget(QLabel("Crossfade s"))
        assembly_row.addWidget(self.crossfade_box)
        assembly_row.addStretch(1)
        assembly_card.body.addLayout(assembly_row)
        layout.addWidget(assembly_card)

        button_row = QHBoxLayout()
        plan_button = QPushButton("Write shotlist only")
        plan_button.clicked.connect(lambda: self.run(plan_only=True))
        run_button = QPushButton("Run the full pipeline")
        run_button.setObjectName("primary")
        run_button.clicked.connect(lambda: self.run(plan_only=False))
        button_row.addWidget(plan_button)
        button_row.addWidget(run_button)
        button_row.addStretch(1)
        layout.addLayout(button_row)
        layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(left)
        splitter.addWidget(scroll)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(QLabel("Shotlist and output"))
        self.output_box = QTextEdit()
        self.output_box.setReadOnly(True)
        right_layout.addWidget(self.output_box, 2)
        self.preview = MediaPreview()
        right_layout.addWidget(self.preview, 2)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        scroll.setMinimumWidth(420)
        right.setMinimumWidth(340)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(8)
        outer.addWidget(splitter)

    def _provider_model_pair(self, capability: Capability) -> tuple[QComboBox, QComboBox]:
        provider_box, model_box = QComboBox(), QComboBox()
        provider_box.setProperty("capability", capability.value)
        model_box.setProperty("capability", capability.value)
        provider_box.currentIndexChanged.connect(
            lambda: self._fill_models(provider_box, model_box, capability)
        )
        return provider_box, model_box

    def reload_providers(self) -> None:
        for provider_box, model_box, capability in (
            (self.llm_provider, self.llm_model, Capability.LLM),
            (self.image_provider, self.image_model, Capability.TEXT_TO_IMAGE),
            (self.video_provider, self.video_model, Capability.TEXT_TO_VIDEO),
            (self.tts_provider, self.tts_model, Capability.TEXT_TO_SPEECH),
        ):
            provider_box.blockSignals(True)
            provider_box.clear()
            for provider in self.context.registry.for_capability(capability):
                suffix = "" if provider.configured else "  (no key)"
                provider_box.addItem(f"{provider.name}{suffix}", provider.key)
            provider_box.blockSignals(False)
            self._fill_models(provider_box, model_box, capability)

    def _fill_models(self, provider_box: QComboBox, model_box: QComboBox,
                     capability: Capability) -> None:
        model_box.clear()
        provider = self.context.registry.get(provider_box.currentData())
        if provider:
            for spec in provider.models_for(capability):
                model_box.addItem(spec.label, spec.id)

    def _add_references(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Identity references", "", "Images (*.png *.jpg *.jpeg *.webp)"
        )
        self._identity_references.extend(Path(path) for path in paths)
        self.identity_label.setText(
            "\n".join(path.name for path in self._identity_references) or "None"
        )

    def _clear_references(self) -> None:
        self._identity_references.clear()
        self.identity_label.setText("None")

    # -- run -----------------------------------------------------------------------------
    def run(self, plan_only: bool) -> None:
        if self.context.project is None:
            QMessageBox.information(self, "No project", "Create or open a project first.")
            return
        brief = self.brief_box.toPlainText().strip()
        if not brief:
            QMessageBox.information(self, "Empty brief", "Describe the video you want.")
            return
        if not self.llm_model.currentData():
            QMessageBox.warning(
                self, "No writer model",
                "The Director needs a text model to write the shotlist. Add an Anthropic, "
                "Google or OpenAI-compatible key in Settings.",
            )
            return

        config = DirectorConfig(
            brief=brief,
            style=self.style_box.text().strip(),
            shot_count=self.shot_count.value(),
            total_seconds=self.total_seconds.value(),
            aspect_ratio=self.aspect_box.currentText(),
            llm_provider=self.llm_provider.currentData(),
            llm_model=self.llm_model.currentData(),
            generate_stills=self.stills_check.isChecked() and not plan_only,
            image_provider=self.image_provider.currentData() or "google",
            image_model=self.image_model.currentData() or "gemini-2.5-flash-image",
            generate_clips=self.clips_check.isChecked() and not plan_only,
            video_provider=self.video_provider.currentData() or "google",
            video_model=self.video_model.currentData() or "veo-3.1-generate-preview",
            generate_narration=self.narration_check.isChecked() and not plan_only,
            tts_provider=self.tts_provider.currentData() or "elevenlabs",
            tts_model=self.tts_model.currentData() or "eleven_multilingual_v2",
            tts_options={"voice_id": self.voice_id_box.text().strip()},
            assemble=self.assemble_check.isChecked() and not plan_only,
            crossfade=self.crossfade_box.value(),
            identity_references=list(self._identity_references),
        )
        width, height = {"16:9": (1920, 1080), "9:16": (1080, 1920),
                         "1:1": (1080, 1080)}[config.aspect_ratio]
        config.output_width, config.output_height = width, height

        director = Director(self.context.registry, self.context.require_project(),
                            self.context.locator)

        def work(job_context):
            if plan_only:
                shots, title, raw = director.plan(config, job_context)
                return DirectorResult(title=title, shots=shots, shotlist_json=raw)
            return director.run(config, job_context)

        job = self.context.jobs.submit(
            "Director: shotlist" if plan_only else "Director: full pipeline",
            work, category="director",
        )
        self.status.emit(f"Job {job.id}: Director started")
        self.output_box.setPlainText("Working... watch the Jobs tab for progress.")

    def show_result(self, result: DirectorResult) -> None:
        lines = [f"TITLE: {result.title}", ""]
        for shot in result.shots:
            lines.append(f"--- Shot {shot.id} ({shot.duration_seconds:g}s) ---")
            lines.append(f"  {shot.summary}")
            lines.append(f"  IMAGE : {shot.image_prompt}")
            lines.append(f"  VIDEO : {shot.video_prompt}")
            if shot.narration:
                lines.append(f"  VOICE : {shot.narration}")
            if shot.still:
                lines.append(f"  still -> {shot.still}")
            if shot.clip:
                lines.append(f"  clip  -> {shot.clip}")
            if shot.error:
                lines.append(f"  ERROR : {shot.error}")
            lines.append("")
        if result.narration_audio:
            lines.append(f"Narration: {result.narration_audio}")
        if result.final_video:
            lines.append(f"FINAL CUT: {result.final_video}")
        if result.warnings:
            lines.append("")
            lines.append("Warnings:")
            lines += [f"  - {warning}" for warning in result.warnings]

        self.output_box.setPlainText("\n".join(lines))
        preview_target = result.final_video or next(
            (shot.clip or shot.still for shot in result.shots if shot.clip or shot.still), None
        )
        self.preview.show_path(preview_target)
        self.library_changed.emit()


def _pair_row(provider_box: QComboBox, model_box: QComboBox) -> QWidget:
    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(provider_box, 1)
    layout.addWidget(model_box, 2)
    return container
