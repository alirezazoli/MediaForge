"""Timeline panel: order clips, set in/out points, render the cut."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
    QMessageBox, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import ffmpeg_tools
from ..context import AppContext
from . import theme
from .widgets import Card, MediaPreview

COLUMNS = ["#", "Clip", "In", "Out", "Length", "Transition"]


class TimelinePanel(QWidget):
    status = Signal(str)
    library_changed = Signal()

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)

        toolbar = QHBoxLayout()
        for label, handler in (
            ("Refresh", self.refresh),
            ("Move up", lambda: self._move(-1)),
            ("Move down", lambda: self._move(1)),
            ("Remove", self._remove),
            ("Clear all", self._clear),
        ):
            button = QPushButton(label)
            button.clicked.connect(handler)
            toolbar.addWidget(button)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.verticalHeader().setVisible(False)
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.currentCellChanged.connect(self._on_row_changed)
        layout.addWidget(self.table, 2)

        bottom = QHBoxLayout()

        render_card = Card("Render settings")
        self.width_box = QSpinBox()
        self.width_box.setRange(320, 7680)
        self.width_box.setValue(1920)
        self.height_box = QSpinBox()
        self.height_box.setRange(240, 4320)
        self.height_box.setValue(1080)
        self.fps_box = QSpinBox()
        self.fps_box.setRange(12, 120)
        self.fps_box.setValue(30)
        self.crossfade_box = QDoubleSpinBox()
        self.crossfade_box.setRange(0.0, 3.0)
        self.crossfade_box.setSingleStep(0.1)
        self.crossfade_box.setValue(0.5)
        self.preset_box = QComboBox()
        self.preset_box.addItems(["Custom", "1920x1080 landscape", "1080x1920 vertical",
                                  "1080x1080 square", "3840x2160 4K"])
        self.preset_box.currentTextChanged.connect(self._apply_preset)
        self.crossfade_box.setSuffix(" s")
        # One setting per row: five label-and-field pairs on one line is what truncated
        # controls to unreadable stubs on narrower windows.
        settings_form = QFormLayout()
        settings_form.setHorizontalSpacing(theme.SPACE_4)
        settings_form.addRow("Frame", self.preset_box)
        size_row = QHBoxLayout()
        size_row.addWidget(self.width_box, 1)
        size_row.addWidget(QLabel("×"))
        size_row.addWidget(self.height_box, 1)
        settings_form.addRow("Size", size_row)
        settings_form.addRow("Frame rate", self.fps_box)
        settings_form.addRow("Crossfade", self.crossfade_box)
        render_card.body.addLayout(settings_form)

        action_row = QHBoxLayout()
        render_button = QPushButton("Render timeline")
        render_button.setObjectName("primary")
        render_button.clicked.connect(self.render)
        music_button = QPushButton("Render with music track...")
        music_button.clicked.connect(self.render_with_music)
        action_row.addWidget(render_button)
        action_row.addWidget(music_button)
        action_row.addStretch(1)
        self.total_label = QLabel("Total: 00:00:00.00")
        action_row.addWidget(self.total_label)
        render_card.body.addLayout(action_row)
        bottom.addWidget(render_card, 2)

        self.preview = MediaPreview()
        bottom.addWidget(self.preview, 1)
        layout.addLayout(bottom, 1)

    def _apply_preset(self, text: str) -> None:
        presets = {
            "1920x1080 landscape": (1920, 1080),
            "1080x1920 vertical": (1080, 1920),
            "1080x1080 square": (1080, 1080),
            "3840x2160 4K": (3840, 2160),
        }
        if text in presets:
            width, height = presets[text]
            self.width_box.setValue(width)
            self.height_box.setValue(height)

    # -- data ---------------------------------------------------------------------------
    def refresh(self) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        if self.context.project is None:
            self.table.blockSignals(False)
            return
        project = self.context.project
        total = 0.0
        for row, clip in enumerate(project.timeline()):
            asset = project.asset(clip.asset_id)
            self.table.insertRow(row)
            out_point = clip.out_point or (asset.duration if asset else 0.0)
            length = max(0.0, out_point - clip.in_point)
            total += length

            index_item = QTableWidgetItem(str(row + 1))
            index_item.setData(Qt.ItemDataRole.UserRole, clip.id)
            index_item.setFlags(index_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 0, index_item)

            name_item = QTableWidgetItem(asset.label if asset else "[missing asset]")
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            name_item.setData(Qt.ItemDataRole.UserRole + 1, str(asset.path) if asset else "")
            self.table.setItem(row, 1, name_item)

            self.table.setItem(row, 2, QTableWidgetItem(f"{clip.in_point:.2f}"))
            self.table.setItem(row, 3, QTableWidgetItem(f"{out_point:.2f}"))

            length_item = QTableWidgetItem(f"{length:.2f}")
            length_item.setFlags(length_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 4, length_item)

            self.table.setItem(row, 5, QTableWidgetItem(clip.transition))
        self.table.blockSignals(False)
        self.total_label.setText(f"Total: {ffmpeg_tools.format_timecode(total)}")

    def _current_clip_id(self) -> int | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _on_row_changed(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        item = self.table.item(row, 1)
        path = item.data(Qt.ItemDataRole.UserRole + 1) if item else ""
        self.preview.show_path(Path(path) if path else None)

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        clip_id = self.table.item(item.row(), 0).data(Qt.ItemDataRole.UserRole)
        project = self.context.project
        if not project or clip_id is None:
            return
        try:
            if item.column() == 2:
                project.update_clip(clip_id, in_point=float(item.text()))
            elif item.column() == 3:
                project.update_clip(clip_id, out_point=float(item.text()))
            elif item.column() == 5:
                project.update_clip(clip_id, transition=item.text().strip() or "cut")
        except ValueError:
            pass
        self.refresh()

    def _move(self, delta: int) -> None:
        clip_id = self._current_clip_id()
        if clip_id is not None and self.context.project:
            self.context.project.move_clip(clip_id, delta)
            self.refresh()

    def _remove(self) -> None:
        clip_id = self._current_clip_id()
        if clip_id is not None and self.context.project:
            self.context.project.remove_clip(clip_id)
            self.refresh()

    def _clear(self) -> None:
        if self.context.project and QMessageBox.question(
            self, "Clear timeline", "Remove every clip from the timeline?"
        ) == QMessageBox.StandardButton.Yes:
            self.context.project.clear_timeline()
            self.refresh()

    # -- render ---------------------------------------------------------------------------
    def render(self, music: Path | None = None) -> None:
        if not self.context.locator.available:
            QMessageBox.warning(
                self, "ffmpeg missing",
                "Install ffmpeg first:  winget install Gyan.FFmpeg",
            )
            return
        project = self.context.require_project()
        clips = project.timeline()
        if not clips:
            QMessageBox.information(self, "Empty timeline", "Add clips from the Library first.")
            return

        locator = self.context.locator
        context = self.context
        width, height = self.width_box.value(), self.height_box.value()
        fps, crossfade = self.fps_box.value(), self.crossfade_box.value()

        segments: list[tuple[Path, float, float, str]] = []
        for clip in clips:
            asset = project.asset(clip.asset_id)
            if not asset or not asset.path.exists():
                continue
            out_point = clip.out_point or asset.duration or 4.0
            segments.append((asset.path, clip.in_point, out_point, asset.kind))
        if not segments:
            QMessageBox.warning(self, "No usable clips", "Every clip points at a missing file.")
            return

        destination = project.renders_dir / "timeline.mp4"
        counter = 1
        while destination.exists():
            destination = project.renders_dir / f"timeline_{counter}.mp4"
            counter += 1

        def work(job_context):
            staged: list[Path] = []
            durations: list[float] = []
            staging = project.root / "cache" / "staged"
            staging.mkdir(parents=True, exist_ok=True)

            for index, (path, in_point, out_point, kind) in enumerate(segments):
                job_context.raise_if_cancelled()
                job_context.progress(0.05 + 0.5 * index / len(segments),
                                     f"Preparing clip {index + 1}/{len(segments)}")
                length = max(0.2, out_point - in_point)
                staged_path = staging / f"seg_{index:03d}.mp4"
                if kind == "image":
                    command = ffmpeg_tools.ken_burns(
                        locator, path, staged_path, seconds=length, fps=fps,
                        width=width, height=height,
                    )
                else:
                    command = ffmpeg_tools.trim(locator, path, staged_path, in_point,
                                                out_point, reencode=True)
                code, log = ffmpeg_tools.run_with_progress(
                    command, should_cancel=lambda: job_context.cancelled
                )
                if code != 0:
                    raise RuntimeError(f"Preparing clip {index + 1} failed:\n{log}")
                staged.append(staged_path)
                durations.append(length)

            job_context.progress(0.6, "Joining clips")
            command = ffmpeg_tools.concat(
                locator, staged, destination, width=width, height=height, fps=fps,
                crossfade=crossfade, durations=durations,
            )
            code, log = ffmpeg_tools.run_with_progress(
                command,
                on_progress=lambda fraction, label: job_context.progress(
                    0.6 + fraction * 0.3, label
                ),
                should_cancel=lambda: job_context.cancelled,
            )
            if code != 0:
                raise RuntimeError(f"Joining clips failed:\n{log}")

            final = destination
            if music and music.exists():
                job_context.progress(0.92, "Adding music")
                final = destination.with_name(destination.stem + "_music.mp4")
                mux = ffmpeg_tools.mux_audio(
                    locator, destination, music, final,
                    video_duration=command.total_duration, keep_original=True, music_volume=0.35,
                    video_has_audio=ffmpeg_tools.probe(locator, destination).has_audio,
                )
                code, log = ffmpeg_tools.run_with_progress(mux)
                if code != 0:
                    raise RuntimeError(f"Adding music failed:\n{log}")

            context.catalogue(final, source="render", label="Timeline render")
            return final

        job = self.context.jobs.submit("Render timeline", work, category="render")
        self.status.emit(f"Job {job.id}: rendering {len(segments)} clips")

    def render_with_music(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a music track", "", "Audio (*.mp3 *.wav *.m4a *.aac)"
        )
        if path:
            self.render(Path(path))
