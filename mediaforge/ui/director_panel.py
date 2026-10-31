"""Director panel: a brief in, a finished cut out -- as four steps.

Brief, crew, cut, result. The brief is the only thing that has to be written; the crew
step says plainly which roles can be filled with what is connected, and the result step
follows the job as it writes, shoots and assembles, instead of sending the user to the
Jobs screen to find out.
"""

from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSpinBox, QTextBrowser,
    QTextEdit, QVBoxLayout, QWidget,
)

from .. import availability
from ..agents import Director, DirectorConfig, DirectorResult
from ..context import AppContext
from ..jobs import Job, JobState
from ..providers import Capability
from . import icons, theme
from .stepper import Step, StepFlow
from .widgets import Card, JobStatusCard, MediaPreview, centred_page

ASPECTS = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080)}
STEP_BRIEF, STEP_CREW, STEP_CUT, STEP_RESULT = range(4)


class DirectorPanel(QWidget):
    status = Signal(str)
    library_changed = Signal()

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        self._identity_references: list[Path] = []
        self._plan_only = False
        self._build()
        self.reload_providers()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.flow = StepFlow([
            Step("brief", "Brief", "Say what the video is for and what should happen."),
            Step("crew", "Crew", "Choose who writes, shoots and narrates."),
            Step("cut", "Cut", "How the shots are joined."),
            Step("result", "Result", "Watch it being made."),
        ], finish_label="Make the film")

        # ---- step 1: the brief --------------------------------------------------------
        brief_page = QWidget()
        layout = QVBoxLayout(brief_page)
        layout.setSpacing(theme.SPACE_3)
        brief_card = Card("Brief")
        self.brief_box = QTextEdit()
        self.brief_box.setPlaceholderText(
            "What is the video for? Who is it aimed at? What should happen, and what should "
            "the viewer feel or do at the end?\n\n"
            "Example: A 20-second Instagram Reel for a specialty coffee roaster in Tehran. "
            "Warm morning light, hands pouring, steam, close macro of beans. Ends on the bag "
            "with the logo. Tone: calm, premium, no dialogue."
        )
        self.brief_box.setMinimumHeight(150)
        self.brief_box.textChanged.connect(lambda: self._step_changed(self.flow.current()))
        brief_card.body.addWidget(self.brief_box)
        layout.addWidget(brief_card)

        shape_card = Card("Shape")
        form = QFormLayout()
        form.setHorizontalSpacing(theme.SPACE_4)
        self.style_box = QLineEdit("cinematic, natural light, shallow depth of field, 35mm")
        self.style_box.setToolTip("Repeated into every shot's prompt, so the shots match.")
        form.addRow("Look", self.style_box)
        self.shot_count = QSpinBox()
        self.shot_count.setRange(1, 20)
        self.shot_count.setValue(4)
        form.addRow("Shots", self.shot_count)
        self.total_seconds = QSpinBox()
        self.total_seconds.setRange(4, 300)
        self.total_seconds.setValue(20)
        self.total_seconds.setSuffix(" s")
        form.addRow("Length", self.total_seconds)
        self.aspect_box = QComboBox()
        self.aspect_box.addItem(icons.icon("monitor"), "16:9", "16:9")
        self.aspect_box.addItem(icons.icon("phone"), "9:16", "9:16")
        self.aspect_box.addItem(icons.icon("crop"), "1:1", "1:1")
        form.addRow("Frame", self.aspect_box)
        shape_card.body.addLayout(form)
        layout.addWidget(shape_card)

        identity_card = Card("Keep consistent (optional)")
        hint = QLabel("Photos of a person, product or logo that must look the same in "
                      "every shot.")
        hint.setObjectName("subtle")
        hint.setWordWrap(True)
        identity_card.body.addWidget(hint)
        identity_row = QHBoxLayout()
        add_button = QPushButton("Add photos")
        add_button.setObjectName("tonal")
        icons.button(add_button, "add", "@ACCENT")
        add_button.clicked.connect(self._add_references)
        identity_row.addWidget(add_button)
        identity_row.addStretch(1)
        clear_button = QPushButton()
        clear_button.setObjectName("iconButton")
        clear_button.setToolTip("Remove all")
        icons.button(clear_button, "close")
        clear_button.clicked.connect(self._clear_references)
        identity_row.addWidget(clear_button)
        identity_card.body.addLayout(identity_row)
        self.identity_list = QListWidget()
        self.identity_list.setMaximumHeight(84)
        self.identity_list.hide()
        identity_card.body.addWidget(self.identity_list)
        layout.addWidget(identity_card)
        layout.addStretch(1)
        self.flow.add_page(centred_page(brief_page, 820))

        # ---- step 2: the crew ---------------------------------------------------------
        crew_page = QWidget()
        layout = QVBoxLayout(crew_page)
        layout.setSpacing(theme.SPACE_3)
        self.llm_provider, self.llm_model = self._provider_model_pair(Capability.LLM)
        self.image_provider, self.image_model = self._provider_model_pair(Capability.TEXT_TO_IMAGE)
        self.video_provider, self.video_model = self._provider_model_pair(Capability.TEXT_TO_VIDEO)
        self.tts_provider, self.tts_model = self._provider_model_pair(Capability.TEXT_TO_SPEECH)

        self.stills_check = QCheckBox("Make a still of each shot first")
        self.stills_check.setToolTip("Locks the composition before any video is generated.")
        self.stills_check.setChecked(True)
        self.clips_check = QCheckBox("Make a video clip of each shot")
        self.clips_check.setChecked(True)
        self.narration_check = QCheckBox("Add a narrator")
        self.voice_id_box = QLineEdit("21m00Tcm4TlvDq8ikWAM")
        self.voice_id_box.setToolTip("The voice to use, as the voice provider names it.")

        self._role_notes: dict[Capability, QLabel] = {}
        for title, glyph, capability, toggle, provider_box, model_box, extra in (
            ("Writer", "text", Capability.LLM, None, self.llm_provider, self.llm_model, None),
            ("Stills", "image", Capability.TEXT_TO_IMAGE, self.stills_check,
             self.image_provider, self.image_model, None),
            ("Camera", "video", Capability.TEXT_TO_VIDEO, self.clips_check,
             self.video_provider, self.video_model, None),
            ("Narrator", "mic", Capability.TEXT_TO_SPEECH, self.narration_check,
             self.tts_provider, self.tts_model, ("Voice", self.voice_id_box)),
        ):
            card = Card(title)
            if toggle is not None:
                card.body.addWidget(toggle)
            role_form = QFormLayout()
            role_form.setHorizontalSpacing(theme.SPACE_4)
            role_form.addRow("Provider", provider_box)
            role_form.addRow("Model", model_box)
            if extra:
                role_form.addRow(*extra)
            card.body.addLayout(role_form)
            note = QLabel("")
            note.setObjectName("subtle")
            note.setWordWrap(True)
            card.body.addWidget(note)
            self._role_notes[capability] = note
            if toggle is not None:
                toggle.toggled.connect(lambda on, p=provider_box, m=model_box, e=extra: (
                    p.setEnabled(on), m.setEnabled(on), e and e[1].setEnabled(on)))
            layout.addWidget(card)
        self.narration_check.toggled.emit(False)
        layout.addStretch(1)
        self.flow.add_page(centred_page(crew_page, 820))

        # ---- step 3: the cut ----------------------------------------------------------
        cut_page = QWidget()
        layout = QVBoxLayout(cut_page)
        layout.setSpacing(theme.SPACE_3)
        assembly_card = Card("Assembly")
        self.assemble_check = QCheckBox("Join the shots into one film")
        self.assemble_check.setChecked(True)
        assembly_card.body.addWidget(self.assemble_check)
        cut_form = QFormLayout()
        cut_form.setHorizontalSpacing(theme.SPACE_4)
        self.crossfade_box = QDoubleSpinBox()
        self.crossfade_box.setRange(0.0, 3.0)
        self.crossfade_box.setSingleStep(0.1)
        self.crossfade_box.setValue(0.5)
        self.crossfade_box.setSuffix(" s")
        cut_form.addRow("Crossfade", self.crossfade_box)
        assembly_card.body.addLayout(cut_form)
        self.assemble_check.toggled.connect(self.crossfade_box.setEnabled)
        layout.addWidget(assembly_card)

        plan_card = Card("Just the plan")
        plan_hint = QLabel("Write the shot list without generating anything — useful for "
                           "checking the idea before spending time or credits on it.")
        plan_hint.setObjectName("subtle")
        plan_hint.setWordWrap(True)
        plan_card.body.addWidget(plan_hint)
        plan_row = QHBoxLayout()
        plan_button = QPushButton("Write the shot list only")
        plan_button.setObjectName("tonal")
        icons.button(plan_button, "list", "@ACCENT")
        plan_button.clicked.connect(lambda: self.run(plan_only=True))
        plan_row.addWidget(plan_button)
        plan_row.addStretch(1)
        plan_card.body.addLayout(plan_row)
        layout.addWidget(plan_card)
        layout.addStretch(1)
        self.flow.add_page(centred_page(cut_page, 820))

        # ---- step 4: the result -------------------------------------------------------
        result_page = QWidget()
        layout = QVBoxLayout(result_page)
        layout.setSpacing(theme.SPACE_3)
        self.job_card = JobStatusCard("Progress")
        self.job_card.cancel_requested.connect(self.context.jobs.cancel)
        layout.addWidget(self.job_card)
        row = QHBoxLayout()
        row.setSpacing(theme.SPACE_3)
        self.shot_list = QListWidget()
        self.shot_list.setMaximumWidth(300)
        self.shot_list.currentItemChanged.connect(self._show_shot)
        row.addWidget(self.shot_list)
        right = QVBoxLayout()
        self.preview = MediaPreview()
        self.preview.setMinimumHeight(260)
        right.addWidget(self.preview, 3)
        self.shot_detail = QTextBrowser()
        self.shot_detail.setOpenExternalLinks(False)
        self.shot_detail.setMaximumHeight(170)
        right.addWidget(self.shot_detail, 1)
        row.addLayout(right, 1)
        layout.addLayout(row, 1)
        self.flow.add_page(centred_page(result_page, 1080))

        outer.addWidget(self.flow, 1)
        self.flow.step_changed.connect(self._step_changed)
        self.flow.finished.connect(lambda: self.run(plan_only=False))
        self.flow.go_to(STEP_BRIEF)
        self._result: DirectorResult | None = None

    def _step_changed(self, index: int) -> None:
        has_brief = bool(self.brief_box.toPlainText().strip())
        if index == STEP_BRIEF:
            self.flow.set_status("" if has_brief else "Write a brief to continue.")
            self.flow.set_next_enabled(has_brief)
        elif index == STEP_CREW:
            ready = bool(self.llm_model.currentData())
            self.flow.set_status("" if ready else
                                 "No writer is connected: add an Anthropic, Google or "
                                 "OpenAI-compatible key, or run Ollama.")
            self.flow.set_next_enabled(ready)
        elif index == STEP_CUT:
            self.flow.set_status("")
            self.flow.set_next_enabled(True)
        else:
            running = self.job_card.bar.isVisible()
            self.flow.set_next_enabled(has_brief and not running)
            if not running and self.job_card.job_id is None:
                self.flow.set_status("Press Make the film to start.")

    # -- providers -------------------------------------------------------------------------
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
            entries = [(provider.name, provider.key, availability.provider_availability(provider))
                       for provider in self.context.registry.for_capability(capability)]
            usable = availability.decorate_combo(provider_box, entries)
            if usable >= 0:
                provider_box.setCurrentIndex(usable)
            self._fill_models(provider_box, model_box, capability)

    def _fill_models(self, provider_box: QComboBox, model_box: QComboBox,
                     capability: Capability) -> None:
        model_box.clear()
        provider = self.context.registry.get(provider_box.currentData())
        if provider:
            for spec in provider.models_for(capability):
                model_box.addItem(spec.label, spec.id)
        note = getattr(self, "_role_notes", {}).get(capability)
        if note is not None:
            state = availability.provider_availability(provider) if provider else None
            note.setText(state.reason if state and not state.usable else "")
            note.setVisible(bool(note.text()))
        if hasattr(self, "flow"):
            self._step_changed(self.flow.current())

    # -- references ------------------------------------------------------------------------
    def _add_references(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Photos to keep consistent", "", "Images (*.png *.jpg *.jpeg *.webp)"
        )
        self._identity_references.extend(Path(path) for path in paths)
        self._refresh_references()

    def _clear_references(self) -> None:
        self._identity_references.clear()
        self._refresh_references()

    def _refresh_references(self) -> None:
        self.identity_list.clear()
        for path in self._identity_references:
            self.identity_list.addItem(QListWidgetItem(icons.icon("image"), path.name))
        self.identity_list.setVisible(bool(self._identity_references))

    # -- run -------------------------------------------------------------------------------
    def run(self, plan_only: bool) -> None:
        if self.context.project is None:
            QMessageBox.information(self, "No project", "Create or open a project first.")
            return
        brief = self.brief_box.toPlainText().strip()
        if not brief:
            QMessageBox.information(self, "Empty brief", "Describe the video you want.")
            self.flow.go_to(STEP_BRIEF)
            return
        if not self.llm_model.currentData():
            QMessageBox.warning(
                self, "No writer model",
                "The Director needs a text model to write the shot list. Add an Anthropic, "
                "Google or OpenAI-compatible key in Settings, or run Ollama.",
            )
            self.flow.go_to(STEP_CREW)
            return

        config = DirectorConfig(
            brief=brief,
            style=self.style_box.text().strip(),
            shot_count=self.shot_count.value(),
            total_seconds=self.total_seconds.value(),
            aspect_ratio=self.aspect_box.currentData(),
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
        config.output_width, config.output_height = ASPECTS[config.aspect_ratio]

        director = Director(self.context.registry, self.context.require_project(),
                            self.context.locator)

        def work(job_context):
            if plan_only:
                shots, title, raw = director.plan(config, job_context)
                return DirectorResult(title=title, shots=shots, shotlist_json=raw)
            return director.run(config, job_context)

        title = "Director: shot list" if plan_only else "Director: film"
        job = self.context.jobs.submit(title, work, category="director")
        self.flow.go_to(STEP_RESULT)
        self.job_card.track(job.id, "<b>Writing the shot list</b>" if plan_only else
                            f"<b>Making the film</b> — {config.shot_count} shots, "
                            f"{config.total_seconds} s, {config.aspect_ratio}")
        self.flow.set_next_enabled(False)
        self.flow.set_status("")
        self.status.emit(f"Job {job.id}: Director started")

    def on_job_update(self, job: Job) -> bool:
        if not self.job_card.show_job(job):
            return False
        if job.state == JobState.DONE and isinstance(job.result, DirectorResult):
            self.show_result(job.result)
        if job.state not in (JobState.QUEUED, JobState.RUNNING):
            self.flow.set_next_enabled(True)
        return True

    def show_result(self, result: DirectorResult) -> None:
        self._result = result
        self.shot_list.clear()
        if result.final_video:
            item = QListWidgetItem(icons.icon("film", "@ACCENT"), "Final cut")
            item.setData(Qt.ItemDataRole.UserRole, -1)
            self.shot_list.addItem(item)
        for index, shot in enumerate(result.shots):
            glyph = "warning" if shot.error else "video" if shot.clip else (
                "image" if shot.still else "list")
            item = QListWidgetItem(icons.icon(glyph), f"Shot {shot.id} · {shot.duration_seconds:g}s")
            item.setToolTip(shot.summary)
            item.setData(Qt.ItemDataRole.UserRole, index)
            self.shot_list.addItem(item)
        if self.shot_list.count():
            self.shot_list.setCurrentRow(0)
        warnings = "".join(f"<li>{html.escape(w)}</li>" for w in result.warnings)
        if warnings and not result.shots:
            self.shot_detail.setHtml(f"<ul>{warnings}</ul>")
        self.library_changed.emit()

    def _show_shot(self, item: QListWidgetItem | None, _previous=None) -> None:
        if item is None or self._result is None:
            return
        index = item.data(Qt.ItemDataRole.UserRole)
        result = self._result
        if index == -1:
            self.preview.show_path(result.final_video)
            parts = [f"<h3>{html.escape(result.title)}</h3>"]
            if result.narration_audio:
                parts.append(f"<p>Narration: {html.escape(Path(result.narration_audio).name)}</p>")
            if result.warnings:
                parts.append("<p><b>Warnings</b></p><ul>" + "".join(
                    f"<li>{html.escape(w)}</li>" for w in result.warnings) + "</ul>")
            self.shot_detail.setHtml("".join(parts))
            return
        shot = result.shots[index]
        self.preview.show_path(shot.clip or shot.still)
        rows = [("What happens", shot.summary), ("Image prompt", shot.image_prompt),
                ("Video prompt", shot.video_prompt), ("Narration", shot.narration),
                ("Problem", shot.error)]
        self.shot_detail.setHtml("".join(
            f"<p><b>{label}</b><br>{html.escape(str(value))}</p>"
            for label, value in rows if value))
