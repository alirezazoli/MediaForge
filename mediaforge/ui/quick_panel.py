"""Quick clip: one page, three choices, one button.

Photos to video exposes every decision across four steps. This asks only what a person
making a clip from their photos actually cares about -- how long, what music, may the AI
make things move -- and decides the rest faithfully (see quickfilm.py). Everything else is
still one click away on the Photos to video screen.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QMessageBox, QProgressBar,
    QPushButton, QScrollArea, QSlider, QVBoxLayout, QWidget,
)

from .. import photo_ai, photofilm, quickfilm
from ..context import AppContext
from ..ffmpeg_tools import IMAGE_SUFFIXES
from ..jobs import Job, JobState
from ..providers import Capability, GenerationRequest, comfy_local
from . import icons
from .photo_clip_panel import PHOTO_FILTER, PhotoStrip
from .widgets import Card, MediaPreview

MUSIC_COMPOSE, MUSIC_FILE, MUSIC_NONE = "compose", "file", "none"


def clock(seconds: float) -> str:
    seconds = int(round(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


class QuickPanel(QWidget):
    status = Signal(str)
    library_changed = Signal()

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        self._music_file: Path | None = None
        self._length_touched = False
        self._job_id: int | None = None
        self._result: Path | None = None
        self._build()
        self._refresh()

    # -- construction -----------------------------------------------------------------------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroller = QScrollArea()
        scroller.setWidgetResizable(True)
        scroller.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroller)
        page = QWidget()
        scroller.setWidget(page)
        row = QHBoxLayout(page)
        row.setContentsMargins(24, 16, 24, 16)
        inner = QWidget()
        inner.setMaximumWidth(1000)
        row.addStretch(1)
        row.addWidget(inner, 8)
        row.addStretch(1)
        layout = QVBoxLayout(inner)
        layout.setSpacing(12)

        heading = QLabel("Quick clip")
        heading.setObjectName("h1")
        layout.addWidget(heading)
        intro = QLabel("Add photos, choose how long, press Make. The photos stay exactly as "
                       "they are: no filters, no rain or fog, nothing painted in. AI only adds "
                       "motion, and any AI shot that changed what is in its photo is dropped "
                       "for a camera move.")
        intro.setObjectName("subtle")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # -- photos ----------------------------------------------------------------------
        photos = Card("Photos")
        self.strip = PhotoStrip()
        self.strip.photos_changed.connect(self._refresh)
        self.strip.reordered.connect(self._refresh)
        photos.body.addWidget(self.strip)
        buttons = QHBoxLayout()
        for label, glyph, handler, role in (
                ("Add photos", "add", self._add_photos, "tonal"),
                ("Add folder", "folder", self._add_folder, "tonal"),
                ("Remove", "remove", self.strip.remove_selected, "ghost"),
                ("Clear", "close", self.strip.clear_all, "ghost")):
            button = QPushButton(label)
            button.setObjectName(role)
            icons.button(button, glyph, "@ACCENT")
            button.clicked.connect(handler)
            buttons.addWidget(button)
        buttons.addStretch(1)
        self.count_label = QLabel("")
        self.count_label.setObjectName("subtle")
        buttons.addWidget(self.count_label)
        photos.body.addLayout(buttons)
        hint = QLabel("Drag photos in to add them; drag one onto another to change the order.")
        hint.setObjectName("faint")
        photos.body.addWidget(hint)
        layout.addWidget(photos)

        # -- the three choices ---------------------------------------------------------------
        choices = Card("Clip")
        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)

        length_row = QHBoxLayout()
        self.length_slider = QSlider(Qt.Orientation.Horizontal)
        self.length_slider.setRange(int(quickfilm.MIN_SECONDS), int(quickfilm.MAX_SECONDS))
        self.length_slider.setSingleStep(5)
        self.length_slider.setPageStep(15)
        self.length_slider.valueChanged.connect(self._length_moved)
        self.length_label = QLabel("")
        self.length_label.setMinimumWidth(110)
        length_row.addWidget(self.length_slider, 1)
        length_row.addWidget(self.length_label)
        form.addRow("Length (up to 3:00)", length_row)

        self.shape_box = QComboBox()
        for key, label in quickfilm.SHAPES.items():
            self.shape_box.addItem(label, key)
        form.addRow("Shape", self.shape_box)

        music_row = QHBoxLayout()
        self.music_box = QComboBox()
        self.music_box.addItem("Compose music for it, on this PC", MUSIC_COMPOSE)
        self.music_box.addItem("My own music file…", MUSIC_FILE)
        self.music_box.addItem("No music", MUSIC_NONE)
        self.music_box.currentIndexChanged.connect(self._music_changed)
        self.mood_box = QComboBox()
        self.mood_box.addItems(list(quickfilm.MUSIC_MOODS))
        music_row.addWidget(self.music_box, 2)
        music_row.addWidget(self.mood_box, 1)
        form.addRow("Music", music_row)
        self.music_note = QLabel("")
        self.music_note.setObjectName("faint")
        self.music_note.setWordWrap(True)
        form.addRow("", self.music_note)

        self.ai_box = QComboBox()
        for key, label in quickfilm.AI_LEVELS.items():
            self.ai_box.addItem(label, key)
        self.ai_box.setCurrentIndex(1)
        self.ai_box.currentIndexChanged.connect(self._refresh)
        form.addRow("AI motion", self.ai_box)
        self.ai_note = QLabel("")
        self.ai_note.setObjectName("faint")
        self.ai_note.setWordWrap(True)
        form.addRow("", self.ai_note)
        choices.body.addLayout(form)
        layout.addWidget(choices)

        # -- make --------------------------------------------------------------------------
        make_row = QHBoxLayout()
        self.make_button = QPushButton("Make the clip")
        self.make_button.setObjectName("primary")
        icons.button(self.make_button, "film", "@ON_ACCENT")
        self.make_button.setMinimumWidth(180)
        self.make_button.clicked.connect(self.make)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("ghost")
        self.cancel_button.clicked.connect(self.cancel)
        self.cancel_button.hide()
        make_row.addWidget(self.make_button)
        make_row.addWidget(self.cancel_button)
        make_row.addStretch(1)
        layout.addLayout(make_row)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.status_label = QLabel("")
        self.status_label.setObjectName("subtle")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        result = Card("Result")
        self.preview = MediaPreview()
        self.preview.setMinimumHeight(260)
        result.body.addWidget(self.preview)
        result_row = QHBoxLayout()
        self.save_button = QPushButton("Save a copy…")
        self.save_button.setObjectName("tonal")
        icons.button(self.save_button, "save", "@ACCENT")
        self.save_button.clicked.connect(self.save_copy)
        self.folder_button = QPushButton("Show in folder")
        self.folder_button.setObjectName("ghost")
        icons.button(self.folder_button, "folder", "@ACCENT")
        self.folder_button.clicked.connect(self.show_in_folder)
        result_row.addWidget(self.save_button)
        result_row.addWidget(self.folder_button)
        result_row.addStretch(1)
        result.body.addLayout(result_row)
        self.report = QLabel("")
        self.report.setObjectName("subtle")
        self.report.setWordWrap(True)
        result.body.addWidget(self.report)
        self.result_card = result
        result.hide()
        layout.addWidget(result)
        layout.addStretch(1)

    # -- state --------------------------------------------------------------------------------
    def _composer_ready(self) -> tuple[bool, str]:
        comfy = self.context.registry.get("comfyui")
        if comfy is None:
            return False, "ComfyUI is not set up."
        return comfy.builtin_status(comfy_local.ACE_STEP_ID)

    def _length(self) -> float:
        return float(self.length_slider.value())

    def _length_moved(self) -> None:
        if self.length_slider.hasFocus() or self.length_slider.isSliderDown():
            self._length_touched = True
        self._refresh()

    def _refresh(self) -> None:
        photos = self.strip.paths()
        count = len(photos)
        self.count_label.setText(f"{count} photo{'s' if count != 1 else ''}" if count else "")
        if not self._length_touched:
            self.length_slider.blockSignals(True)
            self.length_slider.setValue(int(quickfilm.default_length(max(1, count))))
            self.length_slider.blockSignals(False)
        each = quickfilm.seconds_per_photo(self._length(), count) if count else 0.0
        self.length_label.setText(clock(self._length())
                                  + (f"  ·  {each:.1f} s each" if count else ""))

        level = self.ai_box.currentData()
        comfy = self.context.registry.get("comfyui")
        local_ready = bool(comfy and comfy.builtin_status("wan2.2-ti2v-5b")[0])
        if level == "off":
            self.ai_note.setText("Every photo gets a slow camera move. Fast, and nothing is "
                                 "generated.")
        elif not local_ready:
            self.ai_note.setText("ComfyUI is not running, so photos get camera moves. Start "
                                 "it from Generate or Photos to video to add AI motion.")
        else:
            shots = len(quickfilm.ai_shot_indices(count, level)) if count else 0
            minutes = shots * quickfilm.autopilot.local_minutes_per_shot(min(each or 3.4, 3.4))
            self.ai_note.setText(
                (f"{shots} photo{'s' if shots != 1 else ''} move with AI — about "
                 f"{max(1, round(minutes))} min on this PC. " if shots else "")
                + "Each AI shot is checked against its photo; if anything appeared, "
                  "disappeared or changed, that shot gets a camera move instead.")
        self._music_changed()
        self.make_button.setEnabled(count > 0 and self._job_id is None)

    def _music_changed(self) -> None:
        choice = self.music_box.currentData()
        self.mood_box.setVisible(choice == MUSIC_COMPOSE)
        if choice == MUSIC_COMPOSE:
            ready, reason = self._composer_ready()
            self.music_note.setText(
                f"An original {clock(self._length())} track with ACE-Step, made to the "
                "film's length. Yours to use, no credit needed." if ready else
                f"{reason} Choose a file instead, or start ComfyUI.")
        elif choice == MUSIC_FILE:
            self.music_note.setText(self._music_file.name if self._music_file else
                                    "No file chosen yet.")
        else:
            self.music_note.setText("")

    # -- photos -------------------------------------------------------------------------------
    def _add_photos(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Choose photos", "", PHOTO_FILTER)
        self.strip.add_paths([Path(path) for path in paths])

    def _add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder of photos")
        if folder:
            self.strip.add_paths([path for path in sorted(Path(folder).iterdir())
                                  if path.suffix.lower() in IMAGE_SUFFIXES])

    # -- make ---------------------------------------------------------------------------------
    def make(self) -> None:
        photos = self.strip.paths()
        if not photos:
            return
        if self.context.project is None:
            QMessageBox.information(self, "No project", "Create or open a project first.")
            return
        music_choice = self.music_box.currentData()
        if music_choice == MUSIC_FILE and self._music_file is None:
            path, _ = QFileDialog.getOpenFileName(
                self, "Choose music", "", "Audio (*.mp3 *.wav *.m4a *.aac *.flac *.ogg)")
            if not path:
                return
            self._music_file = Path(path)
            self._music_changed()
        compose = music_choice == MUSIC_COMPOSE and self._composer_ready()[0]
        self.start(photos, self._length(), self.ai_box.currentData(),
                   self.shape_box.currentData(),
                   self._music_file if music_choice == MUSIC_FILE else None,
                   self.mood_box.currentText() if compose else "")

    def start(self, photos: list[Path], length: float, ai_level: str, shape: str,
              music: Path | None, compose_mood: str) -> Job:
        """Plans, composes and renders in one job. Public so it can be driven directly."""
        context = self.context
        project = context.require_project()
        destination = project.renders_dir / "quick_clip.mp4"
        counter = 1
        while destination.exists():
            destination = project.renders_dir / f"quick_clip_{counter}.mp4"
            counter += 1

        def work(job_context):
            report = job_context.progress
            quick = quickfilm.plan(photos, context.registry, length, ai_level, shape, music,
                                   learning=context.learning,
                                   progress=lambda f, m: report(0.05 * f, m))
            settings = quick.plan.settings
            if compose_mood:
                report(0.06, "Composing the music on this PC")
                comfy = context.registry.get("comfyui")
                composed = comfy.generate(
                    GenerationRequest(Capability.TEXT_TO_MUSIC, comfy_local.ACE_STEP_ID,
                                      prompt=quickfilm.MUSIC_MOODS[compose_mood],
                                      options={"seconds": min(240.0, quick.length + 2.0)},
                                      output_dir=project.root / "music"),
                    progress=lambda f, m: report(0.06 + 0.08 * f, f"Music: {m}"),
                    should_cancel=lambda: job_context.cancelled)
                settings.music = composed.files[0]
                context.catalogue(settings.music, source="music",
                                  label=f"Composed for a quick clip ({compose_mood})")
            renderer = photo_ai.make_renderer(context, lambda: settings.size)
            path = photofilm.render(
                context.locator, quick.plan.shots, destination, settings,
                project.root / "cache" / "quickclip",
                progress=lambda f, m: report(0.15 + 0.85 * f, m),
                should_cancel=lambda: job_context.cancelled,
                ai_renderer=renderer)
            context.catalogue(path, source="photofilm", label="Quick clip")
            moved = sum(1 for shot in quick.plan.shots if shot.resolved_transform == "AI video")
            dropped = [shot for shot in quick.plan.shots
                       if shot.transform == "AI video" and shot.resolved_transform != "AI video"]
            return {"path": path, "length": quick.length, "photos": len(photos),
                    "ai_moved": moved, "ai_dropped": [f"{s.path.name}: {s.note}" for s in dropped],
                    "notes": quick.notes, "music": settings.music}

        job = context.jobs.submit(f"Quick clip: {len(photos)} photos", work,
                                  category="quickfilm")
        self._job_id = job.id
        self.make_button.setEnabled(False)
        self.cancel_button.show()
        self.progress.setValue(0)
        self.progress.show()
        self.status_label.setText("Starting…")
        self.status.emit(f"Making a {clock(length)} clip from {len(photos)} photos")
        return job

    def cancel(self) -> None:
        if self._job_id is not None:
            self.context.jobs.cancel(self._job_id)
            self.status_label.setText("Cancelling…")

    def on_job_update(self, job: Job) -> None:
        if job.id != self._job_id:
            return
        self.progress.setValue(int(job.progress * 100))
        self.status_label.setText(job.message)
        if job.state in (JobState.QUEUED, JobState.RUNNING):
            return
        self._job_id = None
        self.progress.hide()
        self.cancel_button.hide()
        self._refresh()
        if job.state == JobState.DONE and isinstance(job.result, dict):
            outcome = job.result
            self._result = outcome["path"]
            self.preview.show_path(self._result)
            self.result_card.show()
            lines = [f"{clock(outcome['length'])} from {outcome['photos']} photos"
                     + (f", {outcome['ai_moved']} with AI motion" if outcome["ai_moved"] else "")
                     + (", with music." if outcome["music"] else ", no music.")]
            if outcome["ai_dropped"]:
                lines.append("Kept as a camera move because the AI changed the photo: "
                             + "; ".join(outcome["ai_dropped"]))
            lines += outcome["notes"]
            self.report.setText("\n".join(lines))
            self.status_label.setText(f"Done — {self._result.name}")
            self.library_changed.emit()
        elif job.state == JobState.CANCELLED:
            self.status_label.setText("Cancelled.")
        else:
            self.status_label.setText(f"Could not make the clip: {job.error.splitlines()[0][:200]}"
                                      if job.error else "Could not make the clip.")

    # -- result -------------------------------------------------------------------------------
    def save_copy(self) -> None:
        if not self._result or not self._result.exists():
            return
        target, _ = QFileDialog.getSaveFileName(self, "Save the clip",
                                                str(Path.home() / self._result.name),
                                                "Video (*.mp4)")
        if target:
            import shutil
            shutil.copy2(self._result, target)
            self.status.emit(f"Saved {target}")

    def show_in_folder(self) -> None:
        if self._result:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._result.parent)))
