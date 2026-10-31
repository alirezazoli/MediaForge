"""Enhance panel: local AI — upscale, interpolate, matte, transcribe, stems.

Everything here runs on the user's machine. No API key, no per-use cost, works offline.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QTextEdit,
    QVBoxLayout, QWidget,
)

from .. import ffmpeg_tools, localai
from ..context import AppContext
from ..project import Asset
from .stepper import Step, StepFlow
from .widgets import Card, MediaPreview, OptionsForm
from . import theme

# (label, tool id, capability key)
OPERATIONS: list[tuple[str, str, str]] = [
    ("Upscale image (Real-ESRGAN)", "realesrgan", "upscale_image"),
    ("Upscale image (Real-CUGAN, art)", "realcugan", "upscale_image"),
    ("Upscale image (waifu2x)", "waifu2x", "upscale_image"),
    ("Upscale video (Real-ESRGAN)", "realesrgan", "upscale_video"),
    ("Upscale video (Real-CUGAN)", "realcugan", "upscale_video"),
    ("Interpolate to higher fps (RIFE)", "rife", "interpolate"),
    ("Remove background (rembg)", "rembg", "matte"),
    ("Transcribe + build subtitles (Whisper)", "faster-whisper", "transcribe"),
    ("Separate audio stems (Demucs)", "demucs", "stems"),
    ("Text to speech (Kokoro)", "kokoro", "tts"),
]


def _page(inner: QWidget) -> QWidget:
    """One measure for every step: centred and capped, so a wide window reads well."""
    page = QWidget()
    layout = QHBoxLayout(page)
    layout.setContentsMargins(theme.SPACE_5, theme.SPACE_4, theme.SPACE_5, theme.SPACE_4)
    layout.addStretch(1)
    inner.setMaximumWidth(900)
    layout.addWidget(inner, 6)
    layout.addStretch(1)
    return page


class EnhancePanel(QWidget):
    status = Signal(str)
    library_changed = Signal()

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        self.asset: Asset | None = None
        self._build()
        self._on_operation_changed()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.flow = StepFlow([
            Step("source", "File", "Pick the file to work on."),
            Step("job", "What to do", "Choose an engine and its settings."),
            Step("result", "Result", "Run it and see what came out."),
        ], finish_label="Run")

        left = QWidget()
        layout = QVBoxLayout(left)

        banner = QLabel(
            "These run on this machine and work offline. "
            "Install each engine once from the Model Hub tab."
        )
        banner.setWordWrap(True)
        banner.setObjectName("okText")
        layout.addWidget(banner)

        source_card = Card("Source")
        self.source_label = QLabel("Nothing selected. Send an asset here from the Library.")
        self.source_label.setWordWrap(True)
        source_card.body.addWidget(self.source_label)
        pick_row = QHBoxLayout()
        pick_button = QPushButton("Choose file from disk")
        pick_button.clicked.connect(self._pick_file)
        pick_row.addWidget(pick_button)
        pick_row.addStretch(1)
        source_card.body.addLayout(pick_row)
        layout.addWidget(source_card)
        layout.addStretch(1)
        self.flow.add_page(_page(left))

        # ---- step 2: which engine, and how ------------------------------------
        job_page = QWidget()
        layout = QVBoxLayout(job_page)
        layout.setSpacing(theme.SPACE_3)

        operation_card = Card("Operation")
        self.operation_box = QComboBox()
        for label, tool_id, kind in OPERATIONS:
            self.operation_box.addItem(label, (tool_id, kind))
        self.operation_box.currentIndexChanged.connect(self._on_operation_changed)
        operation_card.body.addWidget(self.operation_box)
        self.tool_status = QLabel("")
        self.tool_status.setWordWrap(True)
        operation_card.body.addWidget(self.tool_status)
        layout.addWidget(operation_card)

        options_card = Card("Options")
        self.options_form = OptionsForm()
        options_card.body.addWidget(self.options_form)
        layout.addWidget(options_card)

        tts_card = Card("Text (for text-to-speech only)")
        self.text_box = QTextEdit()
        self.text_box.setPlaceholderText("Narration script for Kokoro TTS")
        self.text_box.setMaximumHeight(100)
        tts_card.body.addWidget(self.text_box)
        layout.addWidget(tts_card)

        run_row = QHBoxLayout()
        self.run_button = QPushButton("Run")
        self.run_button.setObjectName("primary")
        self.run_button.clicked.connect(self.run)
        install_button = QPushButton("Install this engine")
        install_button.clicked.connect(self._install_current)
        run_row.addWidget(self.run_button)
        run_row.addWidget(install_button)
        run_row.addStretch(1)
        layout.addLayout(run_row)
        layout.addStretch(1)
        self.flow.add_page(_page(job_page))

        # ---- step 3: what came out --------------------------------------------
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(QLabel("Preview"))
        self.preview = MediaPreview()
        right_layout.addWidget(self.preview, 2)
        self.output_box = QTextEdit()
        self.output_box.setReadOnly(True)
        self.output_box.setPlaceholderText("Transcripts and tool output appear here")
        right_layout.addWidget(self.output_box, 1)
        self.flow.add_page(_page(right))

        outer.addWidget(self.flow, 1)
        self.flow.step_changed.connect(self._step_changed)
        self.flow.finished.connect(self.run)
        self.flow.go_to(0)

    def _step_changed(self, index: int) -> None:
        ready = self.asset is not None
        if index == 0:
            self.flow.set_status(self.asset.label if ready else "Pick a file to work on.")
            self.flow.set_next_enabled(ready)
        elif index == 1:
            tool, _ = self._current()
            self.flow.set_status("" if tool and tool.installed
                                 else "This engine is not installed yet.")
            self.flow.set_next_enabled(True)
        else:
            self.flow.set_status("")

    # -- state ---------------------------------------------------------------------------
    def _current(self) -> tuple[localai.LocalTool | None, str]:
        data = self.operation_box.currentData()
        if not data:
            return None, ""
        tool_id, kind = data
        return self.context.local_tools.get(tool_id), kind

    def _on_operation_changed(self) -> None:
        tool, _ = self._current()
        if tool is None:
            self.tool_status.setText("Engine not found in the catalogue.")
            self.options_form.rebuild({})
            return
        self.options_form.rebuild(tool.options)
        if tool.installed:
            self.tool_status.setText(
                f"<span style='color:{theme.SUCCESS}'>{tool.name} is installed.</span>  "
                f"{tool.license} — {tool.summary}"
            )
        else:
            self.tool_status.setText(
                f"<span style='color:{theme.WARNING}'>{tool.name} is not installed "
                f"(~{tool.size_mb} MB).</span> Press 'Install this engine', or use the "
                f"Model Hub tab.<br>{tool.summary}"
            )

    def set_asset(self, asset: Asset) -> None:
        self.asset = asset
        if hasattr(self, 'flow'):
            self.flow._sync()
        self.source_label.setText(
            f"<b>{asset.label}</b><br>"
            f"<span style='color:{theme.TEXT_FAINT}'>{asset.path}</span><br>"
            f"{asset.kind} &middot; {asset.resolution} &middot; "
            f"{ffmpeg_tools.format_timecode(asset.duration)}"
        )
        self.preview.show_path(asset.path)

    def _pick_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose media", "", "Media (*.*)")
        if not path or self.context.project is None:
            return
        asset_id = self.context.catalogue(Path(path), source="import")
        asset = self.context.require_project().asset(asset_id)
        if asset:
            self.set_asset(asset)
            self.library_changed.emit()

    def refresh_status(self) -> None:
        self._on_operation_changed()

    # -- install --------------------------------------------------------------------------
    def _install_current(self) -> None:
        tool, _ = self._current()
        if tool is None:
            return
        if tool.installed:
            QMessageBox.information(self, "Already installed", f"{tool.name} is ready to use.")
            return
        answer = QMessageBox.question(
            self, "Install engine",
            f"Download and install {tool.name}?\n\n"
            f"Source: {tool.repo}\n"
            f"Licence: {tool.license}\n"
            f"Size: about {tool.size_mb} MB\n"
            f"Method: {tool.install_kind}\n\n"
            "Nothing is downloaded until you confirm.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        def work(job_context):
            return localai.install_tool(
                tool, progress=lambda fraction, message: job_context.progress(fraction, message)
            )

        job = self.context.jobs.submit(f"Install {tool.name}", work, category="install")
        self.status.emit(f"Job {job.id}: installing {tool.name}")

    # -- run ------------------------------------------------------------------------------
    def run(self) -> None:
        if self.context.project is None:
            QMessageBox.information(self, "No project", "Create or open a project first.")
            return
        tool, kind = self._current()
        if tool is None:
            return
        if not tool.installed:
            QMessageBox.warning(
                self, "Engine not installed",
                f"{tool.name} is not installed yet.\n\nPress 'Install this engine' first "
                f"(about {tool.size_mb} MB, from {tool.repo}).",
            )
            return
        if kind != "tts" and (self.asset is None or not self.asset.path.exists()):
            QMessageBox.information(self, "No source", "Pick a source file first.")
            return

        project = self.context.require_project()
        options = self.options_form.values()
        context = self.context
        source = self.asset.path if self.asset else None
        locator = self.context.locator

        def target(suffix: str, tag: str) -> Path:
            stem = source.stem if source else "speech"
            candidate = project.renders_dir / f"{stem}_{tag}{suffix}"
            counter = 1
            while candidate.exists():
                candidate = project.renders_dir / f"{stem}_{tag}_{counter}{suffix}"
                counter += 1
            return candidate

        if kind in ("upscale_video", "interpolate") and not locator.available:
            QMessageBox.warning(self, "ffmpeg required",
                                "Video enhancement needs ffmpeg. Install it in Settings.")
            return

        text = self.text_box.toPlainText().strip()

        def work(job_context):
            report = lambda fraction, message: job_context.progress(fraction, message)
            cancel = lambda: job_context.cancelled

            if kind == "upscale_image":
                destination = target(".png", f"x{options.get('scale', 4)}")
                localai.upscale_image(tool, source, destination, options, report, cancel)
                context.catalogue(destination, source="enhance")
                return destination

            if kind == "upscale_video":
                destination = target(".mp4", f"x{options.get('scale', 4)}")
                localai.upscale_video(tool, source, destination, options, locator, report, cancel)
                context.catalogue(destination, source="enhance")
                return destination

            if kind == "interpolate":
                destination = target(".mp4", f"{options.get('factor', 2)}x-fps")
                localai.interpolate_video(tool, source, destination, options, locator,
                                          report, cancel)
                context.catalogue(destination, source="enhance")
                return destination

            if kind == "matte":
                destination = target(".png", "nobg")
                localai.remove_background(tool, source, destination, options, report, cancel)
                context.catalogue(destination, source="enhance")
                return destination

            if kind == "transcribe":
                segments = localai.transcribe(tool, source, options, report, cancel)
                srt = ffmpeg_tools.write_srt(segments, target(".srt", "subs"))
                transcript = "\n".join(
                    f"[{segment['start']:7.2f} - {segment['end']:7.2f}] {segment['text']}"
                    for segment in segments
                )
                return f"Subtitles written to {srt}\n\n{transcript}"

            if kind == "stems":
                folder = project.renders_dir / f"{source.stem}_stems"
                stems = localai.separate_audio(tool, source, folder, options, report, cancel)
                for stem in stems:
                    context.catalogue(stem, source="enhance")
                return "\n".join(str(stem) for stem in stems)

            if kind == "tts":
                if not text:
                    raise RuntimeError("Write the narration text first.")
                destination = target(".wav", "kokoro")
                localai.speak(tool, text, destination, options, report, cancel)
                context.catalogue(destination, source="enhance")
                return destination

            raise RuntimeError(f"Unknown operation '{kind}'")

        if hasattr(self, 'flow'):
            self.flow.go_to(len(self.flow.steps) - 1)
        job = self.context.jobs.submit(
            f"{self.operation_box.currentText()}", work, category="enhance"
        )
        self.status.emit(f"Job {job.id}: {self.operation_box.currentText()}")

    def show_result(self, result) -> None:
        if isinstance(result, Path):
            self.preview.show_path(result)
            self.output_box.setPlainText(str(result))
        elif isinstance(result, str):
            self.output_box.setPlainText(result)
        self.library_changed.emit()
        self.refresh_status()
