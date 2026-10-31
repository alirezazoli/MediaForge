"""Generate panel: one form that drives every provider capability."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel, QListWidget, QMessageBox,
    QPushButton, QScrollArea, QSpinBox, QSplitter, QTextEdit, QVBoxLayout, QWidget,
)

from .. import availability
from ..context import AppContext
from ..providers import Capability, GenerationRequest, GenerationResult, ProviderError
from . import theme
from .widgets import ResponsiveSplitter, Card, MediaPreview, OptionsForm

CAPABILITY_ORDER = [
    Capability.TEXT_TO_VIDEO,
    Capability.IMAGE_TO_VIDEO,
    Capability.TEXT_TO_IMAGE,
    Capability.IMAGE_EDIT,
    Capability.LIP_SYNC,
    Capability.MOTION_TRANSFER,
    Capability.VIDEO_EXTEND,
    Capability.UPSCALE,
    Capability.INTERPOLATE,
    Capability.DENOISE,
    Capability.RESTORE,
    Capability.MATTE,
    Capability.SEPARATE_AUDIO,
    Capability.TEXT_TO_SPEECH,
    Capability.TRANSCRIBE,
    Capability.VIDEO_UNDERSTANDING,
    Capability.LLM,
]

NEEDS_IMAGE = {Capability.IMAGE_EDIT, Capability.IMAGE_TO_VIDEO}
NEEDS_MEDIA = {Capability.TRANSCRIBE, Capability.VIDEO_UNDERSTANDING, Capability.UPSCALE}


class GeneratePanel(QWidget):
    status = Signal(str)
    library_changed = Signal()

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        self._reference_images: list[Path] = []
        self._reference_media: Path | None = None
        self._build()
        self._reload_capabilities()

    def _build(self) -> None:
        outer = QHBoxLayout(self)
        splitter = ResponsiveSplitter()

        # -- left: the request form ---------------------------------------------------
        form_host = QWidget()
        form_layout = QVBoxLayout(form_host)

        target = Card("What to make")
        row = QHBoxLayout()
        self.capability_box = QComboBox()
        self.capability_box.currentIndexChanged.connect(self._reload_providers)
        self.provider_box = QComboBox()
        self.provider_box.currentIndexChanged.connect(self._reload_models)
        self.model_box = QComboBox()
        self.model_box.currentIndexChanged.connect(self._reload_options)
        row.addWidget(QLabel("Task"))
        row.addWidget(self.capability_box, 1)
        row.addWidget(QLabel("Provider"))
        row.addWidget(self.provider_box, 1)
        target.body.addLayout(row)

        self.free_only = QCheckBox("Free only — no API key needed")
        self.free_only.setToolTip(
            "Shows only what runs on your own machine: local ComfyUI graphs and the "
            "local engines from the Model Hub."
        )
        self.free_only.toggled.connect(self._reload_providers)
        target.body.addWidget(self.free_only)
        self.model_note = QLabel("")
        self.model_note.setWordWrap(True)
        self.model_note.setObjectName("subtle")
        target.body.addWidget(self.model_box)
        target.body.addWidget(self.model_note)
        form_layout.addWidget(target)

        prompt_card = Card("Prompt")
        self.prompt_box = QTextEdit()
        self.prompt_box.setPlaceholderText(
            "Describe the shot: subject, action, setting, lighting, lens, camera move, mood."
        )
        self.prompt_box.setMinimumHeight(120)
        self.negative_box = QTextEdit()
        self.negative_box.setPlaceholderText("Negative prompt (what to avoid) -- optional")
        self.negative_box.setMaximumHeight(60)
        prompt_card.body.addWidget(self.prompt_box)
        prompt_card.body.addWidget(self.negative_box)
        form_layout.addWidget(prompt_card)

        self.reference_card = Card("References")
        reference_row = QHBoxLayout()
        add_images = QPushButton("Add image refs")
        add_images.clicked.connect(self._add_reference_images)
        add_media = QPushButton("Set video/audio input")
        add_media.clicked.connect(self._add_reference_media)
        use_selection = QPushButton("Use library selection")
        use_selection.clicked.connect(self.use_library_selection)
        clear_refs = QPushButton("Clear")
        clear_refs.clicked.connect(self._clear_references)
        for button in (add_images, add_media, use_selection, clear_refs):
            reference_row.addWidget(button)
        self.reference_card.body.addLayout(reference_row)
        self.reference_list = QListWidget()
        self.reference_list.setMaximumHeight(90)
        self.reference_card.body.addWidget(self.reference_list)
        form_layout.addWidget(self.reference_card)

        options_card = Card("Model options")
        self.options_form = OptionsForm()
        options_card.body.addWidget(self.options_form)
        seed_row = QHBoxLayout()
        seed_row.addWidget(QLabel("Seed (-1 = random)"))
        self.seed_box = QSpinBox()
        self.seed_box.setRange(-1, 2_147_483_647)
        self.seed_box.setValue(-1)
        seed_row.addWidget(self.seed_box, 1)
        seed_row.addWidget(QLabel("Batch"))
        self.batch_box = QSpinBox()
        self.batch_box.setRange(1, 20)
        self.batch_box.setValue(1)
        seed_row.addWidget(self.batch_box)
        options_card.body.addLayout(seed_row)
        form_layout.addWidget(options_card)

        action_row = QHBoxLayout()
        self.generate_button = QPushButton("Generate")
        self.generate_button.setObjectName("primary")
        self.generate_button.clicked.connect(self.generate)
        self.credential_label = QLabel("")
        self.credential_label.setWordWrap(True)
        action_row.addWidget(self.generate_button)
        action_row.addWidget(self.credential_label, 1)
        form_layout.addLayout(action_row)
        form_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(form_host)
        splitter.addWidget(scroll)

        # -- right: results -----------------------------------------------------------
        results_host = QWidget()
        results_layout = QVBoxLayout(results_host)
        results_layout.addWidget(QLabel("Results"))
        self.result_list = QListWidget()
        self.result_list.currentTextChanged.connect(self._preview_result)
        results_layout.addWidget(self.result_list, 1)
        self.preview = MediaPreview()
        results_layout.addWidget(self.preview, 2)
        self.text_output = QTextEdit()
        self.text_output.setReadOnly(True)
        self.text_output.setPlaceholderText("Text output from LLM / transcription tasks")
        results_layout.addWidget(self.text_output, 1)
        splitter.addWidget(results_host)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        scroll.setMinimumWidth(400)
        results_host.setMinimumWidth(340)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(8)
        outer.addWidget(splitter)

    def _capability(self) -> Capability | None:
        """Qt stores the str-backed enum as a bare string; rebuild the real member.

        This matters beyond cosmetics: Enum hashes by name, so a plain "image_edit"
        would silently miss a `capability in NEEDS_IMAGE` test.
        """
        raw = self.capability_box.currentData()
        return availability.as_capability(raw) if raw else None

    # -- cascading selectors -----------------------------------------------------------
    def _reload_capabilities(self) -> None:
        current = self.capability_box.currentData()
        self.capability_box.blockSignals(True)
        self.capability_box.clear()
        for capability in CAPABILITY_ORDER:
            if self.context.registry.for_capability(capability):
                self.capability_box.addItem(capability.label, capability)
        if current:
            index = self.capability_box.findData(current)
            if index >= 0:
                self.capability_box.setCurrentIndex(index)
        self.capability_box.blockSignals(False)
        self._reload_providers()

    def _reload_providers(self) -> None:
        capability = self._capability()
        entries = []
        if capability:
            for provider in self.context.registry.for_capability(capability):
                state = availability.provider_availability(provider)
                if self.free_only.isChecked() and state.state is not availability.State.FREE:
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
                if self.free_only.isChecked() and state.state is not availability.State.FREE:
                    continue
                entries.append((spec.label, spec.id, state))
        usable = availability.decorate_combo(self.model_box, entries)

        if not entries:
            self.credential_label.setText(
                "Nothing here yet. Turn off 'Free only', or add an API key in Settings."
                if self.free_only.isChecked() else "No models for this task."
            )
            self.credential_label.setObjectName("subtle")
        elif usable < 0:
            names = sorted({name for _, _, state in entries for name in state.needs})
            self.credential_label.setText(
                "Every model here is locked. Add a key for " + (" or ".join(names[:3]) or "a backend")
                + " in Settings — or tick 'Free only' to see what runs with no key."
            )
            self.credential_label.setObjectName("warnText")
        else:
            state = entries[usable][2] if usable < len(entries) else None
            self.credential_label.setText(state.reason if state else "")
            colour = theme.FREE if (state and state.state is availability.State.FREE) else theme.TEXT_MUTED
            self.credential_label.setStyleSheet(f"color:{colour};")

        self._reload_options()

    def _reload_options(self) -> None:
        provider_key = self.provider_box.currentData()
        model_id = self.model_box.currentData()
        provider = self.context.registry.get(provider_key) if provider_key else None
        spec = provider.model(model_id) if provider and model_id else None
        self.options_form.rebuild(spec.options if spec else {})
        self.model_note.setText(spec.notes if spec else "")

        capability = self._capability()
        needs_reference = capability in NEEDS_IMAGE or capability in NEEDS_MEDIA
        self.reference_card.setVisible(True)
        self.reference_card.setEnabled(True)
        if needs_reference and not (self._reference_images or self._reference_media):
            self.status.emit(f"{capability.label} needs a reference file")

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
            self.reference_list.addItem(f"image: {path.name}")
        if self._reference_media:
            self.reference_list.addItem(f"media: {self._reference_media.name}")

    # -- run ------------------------------------------------------------------------------
    def generate(self) -> None:
        if self.context.project is None:
            QMessageBox.information(self, "No project", "Create or open a project first.")
            return
        capability = self._capability()
        provider_key = self.provider_box.currentData()
        model_id = self.model_box.currentData()
        if not (capability and provider_key and model_id):
            QMessageBox.information(self, "Nothing selected", "Pick a task, provider and model.")
            return

        state = availability.model_availability(
            self.context.registry, provider_key, model_id, capability
        )
        if not state.usable:
            QMessageBox.warning(
                self, "This model is locked",
                f"{state.reason}\n\nAdd the key in Settings, or tick 'Free only' to see "
                f"what runs with no key at all.",
            )
            return

        prompt = self.prompt_box.toPlainText().strip()
        if not prompt and capability not in (Capability.TRANSCRIBE, Capability.UPSCALE):
            QMessageBox.information(self, "Empty prompt", "Write a prompt first.")
            return
        if capability in NEEDS_IMAGE and not self._reference_images:
            QMessageBox.information(
                self, "Reference needed", f"{capability.label} needs at least one reference image."
            )
            return
        if capability in NEEDS_MEDIA and not (self._reference_media or self._reference_images):
            QMessageBox.information(
                self, "Input needed", f"{capability.label} needs a video or audio input."
            )
            return

        project = self.context.require_project()
        seed = self.seed_box.value()
        options = self.options_form.values()
        batch = self.batch_box.value()

        request_template = GenerationRequest(
            capability=capability, model_id=model_id, prompt=prompt,
            negative_prompt=self.negative_box.toPlainText().strip(),
            reference_images=list(self._reference_images),
            reference_video=self._reference_media if self._reference_media
            and self._reference_media.suffix.lower() not in {".mp3", ".wav", ".m4a"} else None,
            audio_input=self._reference_media if self._reference_media
            and self._reference_media.suffix.lower() in {".mp3", ".wav", ".m4a"} else None,
            options=options, output_dir=project.media_dir,
            seed=None if seed < 0 else seed,
        )

        for index in range(batch):
            self._submit_one(provider_key, request_template, index, batch)

    def _submit_one(self, provider_key: str, template: GenerationRequest, index: int, batch: int) -> None:
        context = self.context
        project = context.require_project()

        # A fresh copy per job: seeds and outputs must not be shared across batch items.
        import copy
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
        self.status.emit(f"Job {job.id} queued: {label}")

    # -- results ---------------------------------------------------------------------------
    def show_result(self, result: GenerationResult) -> None:
        for path in result.files:
            self.result_list.addItem(str(path))
        if result.files:
            self.result_list.setCurrentRow(self.result_list.count() - 1)
        if result.text:
            self.text_output.setPlainText(result.text)
        self.library_changed.emit()

    def _preview_result(self, text: str) -> None:
        if text:
            self.preview.show_path(Path(text))
