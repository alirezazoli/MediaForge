"""Generate panel: every provider capability, as four steps.

Task, model, prompt, result. The old single form showed seventeen tasks in one dropdown
and every field for every task at once -- a reference-image list on a text-to-speech job,
a negative prompt on a transcription. Now the task is a tile, the model choice says up
front whether it will run, and the prompt step shows only what that task takes.
"""

from __future__ import annotations

import copy
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QSpinBox, QTextEdit, QVBoxLayout, QWidget,
)

from .. import availability
from ..context import AppContext
from ..jobs import Job, JobState
from ..providers import Capability, GenerationRequest, GenerationResult, ProviderError
from . import icons, theme
from .stepper import Step, StepFlow
from .widgets import Card, JobStatusCard, MediaPreview, OptionsForm, TileGrid, centred_page

C = Capability
# (heading, [(capability, glyph, what it does)])
TASK_GROUPS: list[tuple[str, list[tuple[Capability, str, str]]]] = [
    ("Video", [
        (C.TEXT_TO_VIDEO, "video", "Describe a shot and get a clip."),
        (C.IMAGE_TO_VIDEO, "photos", "Bring a still to life with generated motion."),
        (C.VIDEO_EXTEND, "next", "Continue a clip past its last frame."),
        (C.MOTION_TRANSFER, "director", "Move a character the way a reference video moves."),
        (C.LIP_SYNC, "chat", "Make a face speak a given audio track."),
    ]),
    ("Image", [
        (C.TEXT_TO_IMAGE, "image", "Describe a picture and get it."),
        (C.IMAGE_EDIT, "edit", "Change an image, or combine several, with words."),
        (C.MATTE, "layers", "Cut the subject out of its background."),
    ]),
    ("Improve", [
        (C.UPSCALE, "sharpen", "More pixels and recovered detail."),
        (C.INTERPOLATE, "speed", "Smoother motion from generated in-between frames."),
        (C.DENOISE, "sparkle", "Remove grain and compression noise."),
        (C.RESTORE, "repair", "Repair faces and fine detail."),
    ]),
    ("Sound & text", [
        (C.TEXT_TO_SPEECH, "volume", "A voice reading your script."),
        (C.TEXT_TO_MUSIC, "music", "Original music from a description, with or without lyrics."),
        (C.SEPARATE_AUDIO, "sliders", "Split a mix into voice, music and effects."),
        (C.TRANSCRIBE, "captions", "Speech to text and subtitles."),
        (C.VIDEO_UNDERSTANDING, "eye", "Ask questions about a video's content."),
        (C.LLM, "chat", "Write scripts, shot lists and captions."),
    ]),
]
TASK_TIPS = {cap: tip for _, entries in TASK_GROUPS for cap, _, tip in entries}

NEEDS_IMAGE = {Capability.IMAGE_EDIT, Capability.IMAGE_TO_VIDEO}
NEEDS_MEDIA = {Capability.TRANSCRIBE, Capability.VIDEO_UNDERSTANDING, Capability.UPSCALE}
# Tasks whose input is a file rather than words: the prompt is optional for them.
PROMPT_OPTIONAL = {Capability.TRANSCRIBE, Capability.UPSCALE, Capability.INTERPOLATE,
                   Capability.DENOISE, Capability.RESTORE, Capability.MATTE,
                   Capability.SEPARATE_AUDIO}
# Tasks where a negative prompt means something.
TAKES_NEGATIVE = {Capability.TEXT_TO_IMAGE, Capability.TEXT_TO_VIDEO,
                  Capability.IMAGE_TO_VIDEO, Capability.IMAGE_EDIT}
AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a"}
STEP_TASK, STEP_MODEL, STEP_PROMPT, STEP_RESULT = range(4)


class GeneratePanel(QWidget):
    status = Signal(str)
    library_changed = Signal()
    open_settings = Signal()

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        self._reference_images: list[Path] = []
        self._reference_media: Path | None = None
        self._job_ids: set[int] = set()
        self._build()
        self._reload_capabilities()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.flow = StepFlow([
            Step("task", "Task", "Choose what to make."),
            Step("model", "Model", "Pick the model that will make it."),
            Step("prompt", "Prompt", "Describe it, and add any reference files."),
            Step("result", "Result", "Generate, then review what came back."),
        ], finish_label="Generate")

        # ---- step 1: the task ---------------------------------------------------------
        task_page = QWidget()
        layout = QVBoxLayout(task_page)
        layout.setContentsMargins(0, 0, 0, 0)
        groups = [(heading, [(cap.value, cap.label, glyph, tip) for cap, glyph, tip in entries])
                  for heading, entries in TASK_GROUPS]
        self.tiles = TileGrid(groups)
        self.tiles.chosen.connect(self._on_task_chosen)
        layout.addWidget(self.tiles)
        self.empty_note = QLabel("No task has a provider yet. Add a key in Settings, or "
                                 "install a local engine from the Model Hub.")
        self.empty_note.setObjectName("subtle")
        self.empty_note.setWordWrap(True)
        self.empty_note.hide()
        layout.addWidget(self.empty_note)
        layout.addStretch(1)
        self.flow.add_page(centred_page(task_page, 1080))

        # ---- step 2: provider and model -----------------------------------------------
        model_page = QWidget()
        layout = QVBoxLayout(model_page)
        layout.setSpacing(theme.SPACE_3)
        self.task_heading = QLabel("")
        self.task_heading.setObjectName("h2")
        layout.addWidget(self.task_heading)
        model_card = Card("Model")
        form = QFormLayout()
        form.setHorizontalSpacing(theme.SPACE_4)
        self.provider_box = QComboBox()
        self.provider_box.currentIndexChanged.connect(self._reload_models)
        form.addRow("Provider", self.provider_box)
        self.model_box = QComboBox()
        self.model_box.currentIndexChanged.connect(self._reload_options)
        form.addRow("Model", self.model_box)
        model_card.body.addLayout(form)
        self.local_only = QCheckBox("Only what runs without an API key")
        self.local_only.setToolTip(
            "Shows only what runs on your own machine: local ComfyUI graphs and the "
            "local engines from the Model Hub."
        )
        self.local_only.toggled.connect(self._reload_providers)
        model_card.body.addWidget(self.local_only)
        self.model_note = QLabel("")
        self.model_note.setWordWrap(True)
        self.model_note.setObjectName("subtle")
        model_card.body.addWidget(self.model_note)
        self.credential_label = QLabel("")
        self.credential_label.setWordWrap(True)
        model_card.body.addWidget(self.credential_label)
        key_row = QHBoxLayout()
        self.key_button = QPushButton("Add a key")
        self.key_button.setObjectName("tonal")
        icons.button(self.key_button, "settings", "@ACCENT")
        self.key_button.clicked.connect(self._fix_locked_model)
        self.key_button.hide()
        key_row.addWidget(self.key_button)
        key_row.addStretch(1)
        model_card.body.addLayout(key_row)
        layout.addWidget(model_card)
        layout.addStretch(1)
        self.flow.add_page(centred_page(model_page, 760))

        # ---- step 3: prompt, references, options --------------------------------------
        prompt_page = QWidget()
        layout = QVBoxLayout(prompt_page)
        layout.setSpacing(theme.SPACE_3)
        self.prompt_card = Card("Prompt")
        self.prompt_box = QTextEdit()
        self.prompt_box.setPlaceholderText(
            "Describe the shot: subject, action, setting, lighting, lens, camera move, mood."
        )
        self.prompt_box.setMinimumHeight(110)
        self.prompt_box.textChanged.connect(lambda: self._step_changed(self.flow.current()))
        self.prompt_card.body.addWidget(self.prompt_box)
        self.negative_box = QTextEdit()
        self.negative_box.setPlaceholderText("What to avoid (optional)")
        self.negative_box.setMaximumHeight(56)
        self.prompt_card.body.addWidget(self.negative_box)
        layout.addWidget(self.prompt_card)

        self.reference_card = Card("Reference files")
        reference_row = QHBoxLayout()
        reference_row.setSpacing(theme.SPACE_2)
        for label, glyph, slot, tip in (
            ("Images", "image", self._add_reference_images, "Add reference images"),
            ("Video or audio", "video", self._add_reference_media, "Set the video or audio input"),
            ("From Library", "library", self.use_library_selection,
             "Use what is selected in the Library"),
        ):
            button = QPushButton(label)
            button.setObjectName("tonal")
            button.setToolTip(tip)
            icons.button(button, glyph, "@ACCENT")
            button.clicked.connect(slot)
            reference_row.addWidget(button)
        reference_row.addStretch(1)
        clear_refs = QPushButton()
        clear_refs.setObjectName("iconButton")
        clear_refs.setToolTip("Remove all reference files")
        icons.button(clear_refs, "close")
        clear_refs.clicked.connect(self._clear_references)
        reference_row.addWidget(clear_refs)
        self.reference_card.body.addLayout(reference_row)
        self.reference_list = QListWidget()
        self.reference_list.setMaximumHeight(96)
        self.reference_card.body.addWidget(self.reference_list)
        layout.addWidget(self.reference_card)

        options_card = Card("Options")
        self.options_form = OptionsForm()
        options_card.body.addWidget(self.options_form)
        layout.addWidget(options_card)
        # Its own card: the model's options are rebuilt per model, and two forms in one
        # card align their fields on different columns.
        variations_card = Card("Variations")
        extra = QFormLayout()
        extra.setHorizontalSpacing(theme.SPACE_4)
        self.seed_box = QSpinBox()
        self.seed_box.setRange(-1, 2_147_483_647)
        self.seed_box.setValue(-1)
        self.seed_box.setSpecialValueText("Random")
        self.seed_box.setToolTip("The same seed with the same prompt gives the same result.")
        extra.addRow("Seed", self.seed_box)
        self.batch_box = QSpinBox()
        self.batch_box.setRange(1, 20)
        self.batch_box.setValue(1)
        self.batch_box.setToolTip("How many variations to make. Each one is a separate job.")
        extra.addRow("How many", self.batch_box)
        variations_card.body.addLayout(extra)
        layout.addWidget(variations_card)
        layout.addStretch(1)
        self.flow.add_page(centred_page(prompt_page, 820))

        # ---- step 4: results ----------------------------------------------------------
        result_page = QWidget()
        layout = QVBoxLayout(result_page)
        layout.setSpacing(theme.SPACE_3)
        self.job_card = JobStatusCard("Progress")
        self.job_card.cancel_requested.connect(self.context.jobs.cancel)
        layout.addWidget(self.job_card)
        results_row = QHBoxLayout()
        results_row.setSpacing(theme.SPACE_3)
        self.result_list = QListWidget()
        self.result_list.setMaximumWidth(280)
        self.result_list.currentItemChanged.connect(self._preview_result)
        results_row.addWidget(self.result_list)
        self.preview = MediaPreview()
        self.preview.setMinimumHeight(300)
        results_row.addWidget(self.preview, 1)
        layout.addLayout(results_row, 1)
        self.text_output = QTextEdit()
        self.text_output.setReadOnly(True)
        self.text_output.setPlaceholderText("Text answers and transcripts appear here")
        self.text_output.setMaximumHeight(160)
        self.text_output.hide()
        layout.addWidget(self.text_output)
        self.flow.add_page(centred_page(result_page, 1080))

        outer.addWidget(self.flow, 1)
        self.flow.step_changed.connect(self._step_changed)
        self.flow.finished.connect(self.generate)
        self.flow.go_to(STEP_TASK)

    def _step_changed(self, index: int) -> None:
        capability = self._capability()
        if index == STEP_TASK:
            self.flow.set_status(f"Selected: {capability.label}" if capability
                                 else "Choose what to make.")
            self.flow.set_next_enabled(capability is not None)
        elif index == STEP_MODEL:
            self.flow.set_status("")
            self.flow.set_next_enabled(self.model_box.currentData() is not None)
        elif index == STEP_PROMPT:
            missing = self._missing_input()
            self.flow.set_status(missing)
            self.flow.set_next_enabled(not missing)
        else:
            missing = self._missing_input()
            self.flow.set_status(missing or "Press Generate to start.")
            self.flow.set_next_enabled(not missing)

    def _capability(self) -> Capability | None:
        """Qt stores the str-backed enum as a bare string; rebuild the real member.

        This matters beyond cosmetics: Enum hashes by name, so a plain "image_edit"
        would silently miss a `capability in NEEDS_IMAGE` test.
        """
        raw = self.tiles.current()
        return availability.as_capability(raw) if raw else None

    def _missing_input(self) -> str:
        """What still has to be supplied before Generate can work, or ''."""
        capability = self._capability()
        if capability is None:
            return "Choose a task first."
        if self.model_box.currentData() is None:
            return "Choose a model first."
        if capability not in PROMPT_OPTIONAL and not self.prompt_box.toPlainText().strip():
            return "Write a prompt."
        if capability in NEEDS_IMAGE and not self._reference_images:
            return f"{capability.label} needs at least one reference image."
        if capability in NEEDS_MEDIA and not (self._reference_media or self._reference_images):
            return f"{capability.label} needs a video, audio or image input."
        return ""

    # -- cascading selectors -----------------------------------------------------------
    def _reload_capabilities(self) -> None:
        offered = [cap.value for _, entries in TASK_GROUPS for cap, _, _ in entries
                   if self.context.registry.for_capability(cap)]
        self.tiles.set_available(offered)
        self.empty_note.setVisible(not offered)
        if self.tiles.current() not in offered and offered:
            self.tiles.select(offered[0])
        self._reload_providers()

    def _on_task_chosen(self, _value: str) -> None:
        self._reload_providers()
        self.flow.go_to(STEP_MODEL)

    def _reload_providers(self) -> None:
        capability = self._capability()
        if capability is not None:
            self.task_heading.setText(capability.label)
        entries = []
        if capability:
            for provider in self.context.registry.for_capability(capability):
                state = availability.provider_availability_for(
                    self.context.registry, provider, capability)
                if self.local_only.isChecked() and state.state is not availability.State.FREE:
                    continue
                entries.append((provider.name, provider.key, state))
        availability.decorate_combo(self.provider_box, entries)
        self._reload_models()

    def _reload_models(self) -> None:
        capability = self._capability()
        provider_key = self.provider_box.currentData()
        provider = self.context.registry.get(provider_key) if provider_key else None

        entries = []
        if provider and capability:
            for spec in provider.models_for(capability):
                state = availability.model_availability(
                    self.context.registry, provider.key, spec.id, capability
                )
                if self.local_only.isChecked() and state.state is not availability.State.FREE:
                    continue
                entries.append((spec.label, spec.id, state))
        usable = availability.decorate_combo(self.model_box, entries)
        self.key_button.setVisible(bool(entries) and usable < 0)
        self.key_button.setEnabled(True)
        # A local engine that is merely stopped needs starting, not a key.
        self._start_engine = bool(
            provider is not None and provider.key == "comfyui"
            and self.context.comfy_install() is not None and not provider.reachable())
        self.key_button.setText("Start ComfyUI" if self._start_engine else "Add a key")
        icons.button(self.key_button, "play" if self._start_engine else "settings", "@ACCENT")

        if not entries:
            self._set_credential_note(
                "Nothing here yet. Untick 'Only what runs without an API key', or add a key "
                "in Settings." if self.local_only.isChecked() else "No models for this task.",
                "subtle")
        elif usable < 0:
            names = sorted({name for _, _, state in entries for name in state.needs})
            self._set_credential_note(
                "Every model here needs a key. Add one for "
                + (" or ".join(names[:3]) or "a backend")
                + " in Settings, or tick 'Only what runs without an API key'.", "warnText")
        else:
            state = entries[usable][2] if usable < len(entries) else None
            self._set_credential_note(state.reason if state else "", "subtle")

        self._reload_options()

    def _fix_locked_model(self) -> None:
        if not getattr(self, "_start_engine", False):
            self.open_settings.emit()
            return
        context = self.context
        job = context.jobs.submit(
            "Start ComfyUI",
            lambda job_context: context.start_comfyui(
                lambda fraction, message: job_context.progress(fraction, message),
                lambda: job_context.cancelled),
            category="engine")
        self.key_button.setEnabled(False)
        self._set_credential_note("Starting ComfyUI… about half a minute.", "subtle")
        self.status.emit(f"Job {job.id}: starting ComfyUI")

    def _set_credential_note(self, text: str, style: str) -> None:
        self.credential_label.setText(text)
        self.credential_label.setObjectName(style)
        self.credential_label.style().unpolish(self.credential_label)
        self.credential_label.style().polish(self.credential_label)

    def _reload_options(self) -> None:
        provider_key = self.provider_box.currentData()
        model_id = self.model_box.currentData()
        provider = self.context.registry.get(provider_key) if provider_key else None
        spec = provider.model(model_id) if provider and model_id else None
        self.options_form.rebuild(spec.options if spec else {})
        self.model_note.setText(spec.notes if spec else "")

        capability = self._capability()
        takes_files = capability in NEEDS_IMAGE or capability in NEEDS_MEDIA or (
            capability in PROMPT_OPTIONAL)
        self.reference_card.setVisible(bool(capability) and (
            takes_files or capability in {Capability.VIDEO_EXTEND, Capability.LIP_SYNC,
                                          Capability.MOTION_TRANSFER}))
        self.negative_box.setVisible(capability in TAKES_NEGATIVE)
        self.prompt_card.setVisible(capability not in {Capability.SEPARATE_AUDIO})
        self._step_changed(self.flow.current())

    # -- references ---------------------------------------------------------------------
    def _add_reference_images(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select reference images", "", "Images (*.png *.jpg *.jpeg *.webp)"
        )
        self._reference_images.extend(Path(path) for path in paths)
        self._refresh_reference_list()

    def _add_reference_media(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select video or audio", "",
            "Media (*.mp4 *.mov *.mkv *.webm *.mp3 *.wav *.m4a)"
        )
        if path:
            self._reference_media = Path(path)
            self._refresh_reference_list()

    def use_library_selection(self) -> None:
        """Hooked up by MainWindow to the library's current selection."""
        assets = getattr(self, "_selection_provider", lambda: [])()
        for asset in assets:
            if asset.kind == "image":
                self._reference_images.append(asset.path)
            else:
                self._reference_media = asset.path
        self._refresh_reference_list()

    def set_selection_provider(self, provider) -> None:
        self._selection_provider = provider

    def _clear_references(self) -> None:
        self._reference_images.clear()
        self._reference_media = None
        self._refresh_reference_list()

    def _refresh_reference_list(self) -> None:
        self.reference_list.clear()
        for path in self._reference_images:
            self.reference_list.addItem(QListWidgetItem(icons.icon("image"), path.name))
        if self._reference_media:
            glyph = "sound" if self._reference_media.suffix.lower() in AUDIO_SUFFIXES else "video"
            self.reference_list.addItem(QListWidgetItem(icons.icon(glyph),
                                                        self._reference_media.name))
        self._step_changed(self.flow.current())

    # -- run ------------------------------------------------------------------------------
    def generate(self) -> None:
        if self.context.project is None:
            QMessageBox.information(self, "No project", "Create or open a project first.")
            return
        missing = self._missing_input()
        if missing:
            QMessageBox.information(self, "Not ready", missing)
            self.flow.go_to(STEP_TASK if self._capability() is None
                            else STEP_MODEL if self.model_box.currentData() is None
                            else STEP_PROMPT)
            return
        capability = self._capability()
        provider_key = self.provider_box.currentData()
        model_id = self.model_box.currentData()

        state = availability.model_availability(
            self.context.registry, provider_key, model_id, capability
        )
        if not state.usable:
            # A stopped local engine is not missing a key; telling the user to add one
            # sends them to the wrong screen.
            if "key" in state.reason.lower():
                title, advice = "This model needs a key", (
                    "Add the key in Settings, or tick 'Only what runs without an API key' "
                    "to see what runs with no key at all.")
            else:
                title, advice = "This model cannot run yet", (
                    "Press 'Start ComfyUI' on this step, or start it yourself."
                    if provider_key == "comfyui" else "")
            QMessageBox.warning(self, title, f"{state.reason}\n\n{advice}".strip())
            self.flow.go_to(STEP_MODEL)
            return

        project = self.context.require_project()
        seed = self.seed_box.value()
        media = self._reference_media
        is_audio = bool(media and media.suffix.lower() in AUDIO_SUFFIXES)
        request_template = GenerationRequest(
            capability=capability, model_id=model_id,
            prompt=self.prompt_box.toPlainText().strip(),
            negative_prompt=(self.negative_box.toPlainText().strip()
                             if capability in TAKES_NEGATIVE else ""),
            reference_images=list(self._reference_images),
            reference_video=media if media and not is_audio else None,
            audio_input=media if is_audio else None,
            options=self.options_form.values(), output_dir=project.media_dir,
            seed=None if seed < 0 else seed,
        )

        batch = self.batch_box.value()
        self.flow.go_to(STEP_RESULT)
        last_job = None
        for index in range(batch):
            last_job = self._submit_one(provider_key, request_template, index, batch)
        if last_job is not None:
            summary = (f"<b>{capability.label}</b> with <b>{model_id}</b>"
                       + (f" — {batch} variations, results appear below as each finishes"
                          if batch > 1 else ""))
            self.job_card.track(last_job.id, summary)
        self.flow.set_status("")

    def _submit_one(self, provider_key: str, template: GenerationRequest, index: int,
                    batch: int) -> Job:
        context = self.context
        project = context.require_project()

        # A fresh copy per job: seeds and outputs must not be shared across batch items.
        request = copy.deepcopy(template)
        if request.seed is not None:
            request.seed += index

        def work(job_context):
            provider = context.registry.get(provider_key)
            if provider is None:
                raise ProviderError(f"Provider '{provider_key}' is no longer available.")
            try:
                result: GenerationResult = provider.generate(
                    request,
                    progress=lambda fraction, message: job_context.progress(fraction, message),
                    should_cancel=lambda: job_context.cancelled,
                )
            except ProviderError as error:
                project.log_generation(
                    provider_key, request.model_id, request.capability.value, request.prompt,
                    request.options, [], status="failed", error=str(error),
                )
                raise

            for path in result.files:
                context.catalogue(path, source="generated", label=request.prompt[:60] or path.stem)
            project.log_generation(
                provider_key, request.model_id, request.capability.value, request.prompt,
                request.options, result.files, text=result.text, elapsed=result.elapsed_seconds,
            )
            return result

        label = template.model_id if batch == 1 else f"{template.model_id} [{index + 1}/{batch}]"
        job = context.jobs.submit(f"Generate: {label}", work, category="generate")
        self._job_ids.add(job.id)
        self.status.emit(f"Job {job.id} queued: {label}")
        return job

    # -- results ---------------------------------------------------------------------------
    def on_job_update(self, job: Job) -> bool:
        """A job this panel started reports here. Returns False for anyone else's job
        (the assistant can generate too), which the main window then handles as before."""
        if job.id not in self._job_ids:
            return False
        self.job_card.show_job(job)
        if job.state == JobState.DONE and isinstance(job.result, GenerationResult):
            self.show_result(job.result)
        elif job.state == JobState.FAILED and job.id != self.job_card.job_id:
            self.status.emit(f"A variation failed: {job.error.strip().splitlines()[-1][:160]}"
                             if job.error.strip() else "A variation failed.")
        if job.state in (JobState.DONE, JobState.FAILED, JobState.CANCELLED):
            self._job_ids.discard(job.id)
        return True

    def show_result(self, result: GenerationResult) -> None:
        for path in result.files:
            glyph = "image" if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} else (
                "sound" if path.suffix.lower() in AUDIO_SUFFIXES else "video")
            item = QListWidgetItem(icons.icon(glyph), path.name)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setToolTip(str(path))
            self.result_list.addItem(item)
        if result.files:
            self.result_list.setCurrentRow(self.result_list.count() - 1)
        if result.text:
            self.text_output.setPlainText(result.text)
            self.text_output.show()
        self.library_changed.emit()

    def _preview_result(self, item: QListWidgetItem | None, _previous=None) -> None:
        if item is not None:
            self.preview.show_path(Path(item.data(Qt.ItemDataRole.UserRole)))
