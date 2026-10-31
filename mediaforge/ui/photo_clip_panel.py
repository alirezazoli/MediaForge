"""Photos in, video out.\n
Two levels of control, deliberately: a single Auto button that decides everything and
says why, and a per-photo inspector that lets any one of those decisions be overridden.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QMessageBox, QProgressBar, QPushButton, QScrollArea,
    QFormLayout, QSlider, QSpinBox, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from .. import atmosphere as atmo
from .. import repair as repair_module
from .. import autopilot, learning, photofilm, sceneanalysis
from ..context import AppContext
from ..ffmpeg_tools import IMAGE_SUFFIXES
from ..photofilm import (
    ALL_MOVES, ASPECTS, FIT_MODES, PhotoFilmSettings, Shot, TRANSFORMS,
)
from ..providers import Capability, GenerationRequest
from . import theme
from .shot_inspector import ShotInspector
from .stepper import Step, StepFlow
from .widgets import Card, MediaPreview

PHOTO_FILTER = "Photos (" + " ".join(f"*{s}" for s in sorted(IMAGE_SUFFIXES)) + ");;All files (*.*)"

# The "leave this setting alone" entry in the apply-to-all row.
UNCHANGED = "Don't change"

TRANSFORM_BADGE = {
    "AI video": ("AI", theme.ACCENT),
    "Camera move (free)": ("move", theme.FREE),
    "Still": ("still", theme.TEXT_FAINT),
    "Auto": ("auto", theme.TEXT_MUTED),
}


class PhotoStrip(QListWidget):
    """Thumbnail strip that accepts dropped files and reorders by drag."""

    photos_changed = Signal()
    reordered = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setIconSize(QSize(148, 100))
        self.setGridSize(QSize(166, 150))
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setMovement(QListWidget.Movement.Static)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setAcceptDrops(True)
        self.setMinimumHeight(190)
        self.setWordWrap(True)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            paths = []
            for url in event.mimeData().urls():
                path = Path(url.toLocalFile())
                if path.is_dir():
                    paths += [child for child in sorted(path.iterdir())
                              if child.suffix.lower() in IMAGE_SUFFIXES]
                elif path.suffix.lower() in IMAGE_SUFFIXES:
                    paths.append(path)
            self.add_paths(paths)
            event.acceptProposedAction()
            return
        super().dropEvent(event)
        self.reordered.emit()

    def add_paths(self, paths: list[Path]) -> int:
        existing = {self.item(row).data(Qt.ItemDataRole.UserRole)
                    for row in range(self.count())}
        added = 0
        for path in paths:
            if str(path) in existing or not path.exists():
                continue
            item = QListWidgetItem(path.name)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setToolTip(str(path))
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                item.setIcon(QIcon(pixmap.scaled(
                    296, 200, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)))
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
            self.addItem(item)
            added += 1
        if added:
            self.photos_changed.emit()
        return added

    def paths(self) -> list[Path]:
        return [Path(self.item(row).data(Qt.ItemDataRole.UserRole))
                for row in range(self.count())]

    def remove_selected(self) -> None:
        for item in self.selectedItems():
            self.takeItem(self.row(item))
        self.photos_changed.emit()

    def clear_all(self) -> None:
        self.clear()
        self.photos_changed.emit()


def _page(inner: QWidget) -> QWidget:
    """Wraps a step's content so every page has the same margins and max width.

    A pane that stretches to 1900px puts the label and its control at opposite ends of
    the screen. Capping the measure is what makes a wide window readable rather than
    merely large.
    """
    page = QWidget()
    layout = QHBoxLayout(page)
    layout.setContentsMargins(theme.SPACE_5, theme.SPACE_5, theme.SPACE_5, theme.SPACE_5)
    layout.addStretch(1)
    inner.setMaximumWidth(1080)
    layout.addWidget(inner, 6)
    layout.addStretch(1)
    return page


class PhotoClipPanel(QWidget):
    status = Signal(str)
    library_changed = Signal()

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        self._shots: dict[str, Shot] = {}      # keyed by path string
        self._music: Path | None = None
        self._job_id: int | None = None
        self._result: Path | None = None
        self._plan_report = ""
        self._build()
        self._refresh()

    # -- construction ---------------------------------------------------------------------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(10)

        header = QHBoxLayout()
        heading = QLabel("Photos to video")
        heading.setObjectName("h1")
        header.addWidget(heading)
        header.addStretch(1)
        self.count_label = QLabel("")
        self.count_label.setObjectName("subtle")
        header.addWidget(self.count_label)
        outer.addLayout(header)

        sub = QLabel("")
        self.subtitle = sub
        sub.setObjectName("subtle")
        sub.setWordWrap(True)
        outer.addWidget(sub)

        # States the AI situation up front. Asking for AI and silently getting a camera
        # move instead is the kind of thing that makes the app feel broken.
        self.ai_banner = QLabel("")
        self.ai_banner.setWordWrap(True)
        self.ai_banner.setVisible(False)
        outer.addWidget(self.ai_banner)

        # One thing at a time. The three-column version showed every control at once,
        # which leaves a newcomer with no first move and no sense of progress.
        self.flow = StepFlow([
            Step("photos", "Photos", "Add the photos you want in the video."),
            Step("look", "Look", "Decide how each photo moves. Auto can do all of it."),
            Step("film", "Film", "Shape, pace, and the music bed."),
            Step("create", "Create", "Check it over, then render."),
        ], finish_label="Create video")

        # ============ step 1: strip + global controls ============
        left_page = QWidget()
        left_layout = QVBoxLayout(left_page)
        left_layout.setContentsMargins(0, 0, 8, 0)

        self.strip = PhotoStrip()
        self.strip.photos_changed.connect(self._photos_changed)
        self.strip.reordered.connect(self._refresh)
        self.strip.currentItemChanged.connect(self._selection_changed)
        left_layout.addWidget(self.strip, 1)

        self.empty_hint = QLabel("Drag photos here  ·  or press “Add photos”")
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_hint.setObjectName("faint")
        left_layout.addWidget(self.empty_hint)

        row = QHBoxLayout()
        for label, handler, style in (
            ("Add photos", self.add_photos, ""),
            ("Add folder", self.add_folder, ""),
            ("Remove", self.strip.remove_selected, "ghost"),
            ("Clear", self.strip.clear_all, "ghost"),
        ):
            button = QPushButton(label)
            if style:
                button.setObjectName(style)
            button.clicked.connect(handler)
            row.addWidget(button)
        row.addStretch(1)
        left_layout.addLayout(row)

        # -- the Auto bar --------------------------------------------------------------
        # Four label+control pairs on one row cannot fit any realistic column width --
        # they truncate into "rytl" and "Don't c". One control per row, label above it,
        # is boring and always readable, which is the trade this panel wants.
        left_layout.addStretch(1)
        self.flow.add_page(_page(left_page))

        # ============ step 2: how each photo moves ============
        look_page = QWidget()
        left_layout = QVBoxLayout(look_page)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(theme.SPACE_3)

        auto_card = Card("Auto")
        self.auto_button = QPushButton("Decide everything for me")
        self.auto_button.setObjectName("primary")
        self.auto_button.setMinimumHeight(38)
        self.auto_button.clicked.connect(self.run_autopilot)
        auto_card.body.addWidget(self.auto_button)

        auto_form = QFormLayout()
        auto_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        auto_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        auto_form.setHorizontalSpacing(14)

        self.ai_policy = QComboBox()
        self.ai_policy.addItems(["Where it helps", "Every photo", "Never (camera moves only)"])
        self.ai_policy.setToolTip(
            "Where it helps: real 3D movement on wide exteriors and aerials, free "
            "camera moves elsewhere.\nEvery photo: costs the most.\n"
            "Never: nothing leaves your machine."
        )
        self.ai_policy.currentTextChanged.connect(self._refresh)
        auto_form.addRow("Use AI", self.ai_policy)

        self.target_length = QSpinBox()
        self.target_length.setRange(0, 600)
        self.target_length.setSuffix(" s")
        self.target_length.setSpecialValueText("auto")
        self.target_length.setToolTip("Total length to aim for. 'auto' lets the pace suit "
                                      "the photos.")
        auto_form.addRow("Target length", self.target_length)
        auto_card.body.addLayout(auto_form)

        self.reorder_check = QCheckBox("Let Auto reorder my photos")
        self.reorder_check.setToolTip(
            "Off: your photos stay in the order you put them in.\n\n"
            "On: Auto rearranges them into a viewing order — aerial and wide "
            "establishing shots first, then daylight exteriors, then interiors, then "
            "close details, with night shots last."
        )
        auto_card.body.addWidget(self.reorder_check)
        left_layout.addWidget(auto_card)

        # -- global overrides ----------------------------------------------------------
        globals_card = Card("Change all photos at once")
        hint = QLabel("Anything left on “Don’t change” keeps whatever each photo "
                      "already has.")
        hint.setObjectName("faint")
        hint.setWordWrap(True)
        globals_card.body.addWidget(hint)

        globals_form = QFormLayout()
        globals_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        globals_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        globals_form.setHorizontalSpacing(14)
        self.global_transform = QComboBox()
        self.global_transform.addItems([UNCHANGED] + TRANSFORMS)
        self.global_move = QComboBox()
        self.global_move.addItems([UNCHANGED] + ALL_MOVES)
        self.global_atmosphere = QComboBox()
        self.global_atmosphere.addItems([UNCHANGED, "Auto"] + atmo.ATMOSPHERE_NAMES)
        for label, widget in (("Animate", self.global_transform), ("Move", self.global_move),
                              ("Atmosphere", self.global_atmosphere)):
            widget.setToolTip(
                f"“{UNCHANGED}” leaves each photo's own {label.lower()} setting alone. "
                f"Pick anything else and pressing Apply sets it on every photo."
            )
            globals_form.addRow(label, widget)
        globals_card.body.addLayout(globals_form)

        apply_row = QHBoxLayout()
        apply_row.addStretch(1)
        apply_button = QPushButton("Apply to all")
        apply_button.clicked.connect(self.apply_globals)
        apply_row.addWidget(apply_button)
        globals_card.body.addLayout(apply_row)
        left_layout.addWidget(globals_card)
        left_layout.addStretch(1)
        self.flow.add_page(_page(look_page))

        # ============ step 3: shape, pace and music ============
        self.tabs = QTabWidget()

        film_page = QWidget()
        film_layout = QVBoxLayout(film_page)
        # Room on the right for the scrollbar, or it sits on top of the combo arrows.
        film_layout.setContentsMargins(0, 0, 10, 0)

        shape_card = Card("Shape and pace")
        self.aspect_box = QComboBox()
        self.aspect_box.addItems(list(ASPECTS.keys()))
        self.aspect_box.currentTextChanged.connect(self._refresh)
        shape_card.body.addWidget(self.aspect_box)

        self.seconds_label = QLabel()
        self.seconds_label.setObjectName("subtle")
        self.seconds_slider = QSlider(Qt.Orientation.Horizontal)
        self.seconds_slider.setRange(10, 120)
        self.seconds_slider.setValue(40)
        self.seconds_slider.valueChanged.connect(self._seconds_changed)
        shape_card.body.addWidget(self.seconds_label)
        shape_card.body.addWidget(self.seconds_slider)

        self.crossfade_label = QLabel()
        self.crossfade_label.setObjectName("subtle")
        self.crossfade_slider = QSlider(Qt.Orientation.Horizontal)
        self.crossfade_slider.setRange(0, 25)
        self.crossfade_slider.setValue(8)
        self.crossfade_slider.valueChanged.connect(self._refresh)
        shape_card.body.addWidget(self.crossfade_label)
        shape_card.body.addWidget(self.crossfade_slider)

        self.fit_box = QComboBox()
        self.fit_box.addItems(list(FIT_MODES.keys()))
        shape_card.body.addWidget(QLabel("Photos that don't match the shape"))
        shape_card.body.addWidget(self.fit_box)
        film_layout.addWidget(shape_card)

        music_card = Card("Music")
        music_row = QHBoxLayout()
        self.music_label = QLabel("None")
        self.music_label.setObjectName("subtle")
        self.music_label.setWordWrap(True)
        choose_music = QPushButton("Choose")
        choose_music.clicked.connect(self.choose_music)
        clear_music = QPushButton("Clear")
        clear_music.setObjectName("ghost")
        clear_music.clicked.connect(self.clear_music)
        music_row.addWidget(self.music_label, 1)
        music_card.body.addLayout(music_row)
        music_buttons = QHBoxLayout()
        music_buttons.addWidget(choose_music)
        music_buttons.addWidget(clear_music)
        music_buttons.addStretch(1)
        music_card.body.addLayout(music_buttons)
        self.music_volume = QSlider(Qt.Orientation.Horizontal)
        self.music_volume.setRange(0, 100)
        self.music_volume.setValue(50)
        music_card.body.addWidget(QLabel("Volume"))
        music_card.body.addWidget(self.music_volume)
        film_layout.addWidget(music_card)

        quality_card = Card("Output")
        quality_row = QHBoxLayout()
        quality_row.addWidget(QLabel("Frame rate"))
        self.fps_box = QSpinBox()
        self.fps_box.setRange(12, 60)
        self.fps_box.setValue(30)
        quality_row.addWidget(self.fps_box)
        quality_row.addWidget(QLabel("Quality"))
        self.crf_box = QSpinBox()
        self.crf_box.setRange(14, 30)
        self.crf_box.setValue(19)
        self.crf_box.setToolTip("Lower is better and bigger. 19 is a good default.")
        quality_row.addWidget(self.crf_box)
        quality_row.addStretch(1)
        quality_card.body.addLayout(quality_row)
        film_layout.addWidget(quality_card)
        film_layout.addStretch(1)

        film_scroll = QScrollArea()
        film_scroll.setWidgetResizable(True)
        film_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        film_scroll.setFrameShape(QFrame.Shape.NoFrame)
        film_scroll.setWidget(film_page)
        self.tabs.addTab(film_scroll, "Film")

        self.inspector = ShotInspector(self.context)
        self.inspector.changed.connect(self._shot_edited)
        self.inspector.analyse_requested.connect(self._analyse_current)
        self.tabs.addTab(self.inspector, "This photo")

        self.report_box = QTextEdit()
        self.report_box.setReadOnly(True)
        self.report_box.setPlaceholderText(
            "Press Auto and the reasoning behind every choice appears here."
        )
        self.tabs.addTab(self.report_box, "Reasoning")
        self.flow.add_page(_page(self.tabs))

        # ============ step 3: preview + go ============
        right = QWidget()
        right_layout = QVBoxLayout(right)
        preview_title = QLabel("Preview")
        preview_title.setObjectName("h2")
        right_layout.addWidget(preview_title)
        self.preview = MediaPreview()
        right_layout.addWidget(self.preview, 1)

        self.estimate_label = QLabel()
        self.estimate_label.setObjectName("h2")
        right_layout.addWidget(self.estimate_label)
        self.cost_label = QLabel("")
        self.cost_label.setObjectName("faint")
        self.cost_label.setWordWrap(True)
        right_layout.addWidget(self.cost_label)

        self.create_button = QPushButton("Create video")
        self.create_button.setObjectName("big")
        self.create_button.clicked.connect(self.create)
        right_layout.addWidget(self.create_button)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        right_layout.addWidget(self.progress)
        self.progress_label = QLabel("")
        self.progress_label.setObjectName("subtle")
        self.progress_label.setWordWrap(True)
        right_layout.addWidget(self.progress_label)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("danger")
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self.cancel)
        right_layout.addWidget(self.cancel_button)

        result_row = QHBoxLayout()
        self.save_button = QPushButton("Save a copy...")
        self.save_button.clicked.connect(self.save_copy)
        self.save_button.setEnabled(False)
        self.folder_button = QPushButton("Show in folder")
        self.folder_button.setObjectName("ghost")
        self.folder_button.clicked.connect(self.show_in_folder)
        self.folder_button.setEnabled(False)
        result_row.addWidget(self.save_button)
        result_row.addWidget(self.folder_button)
        result_row.addStretch(1)
        right_layout.addLayout(result_row)
        self.flow.add_page(_page(right))

        outer.addWidget(self.flow, 1)
        self.flow.step_changed.connect(self._step_changed)
        self.flow.finished.connect(self.create)
        self.flow.go_to(0)

    def _step_changed(self, index: int) -> None:
        """Keeps the bar's status honest about what this step needs."""
        count = self.strip.count()
        # The heading follows the step, instead of describing step two while the user
        # is looking at step one.
        self.subtitle.setText(self.flow.steps[index].hint if 0 <= index < 4 else "")
        if index == 0:
            self.flow.set_status(f"{count} photo(s) added."
                                 if count else "Add at least one photo to continue.")
            self.flow.set_next_enabled(count > 0)
        elif index == 1:
            self.flow.set_status("Press Auto, or set each photo yourself.")
            self.flow.set_next_enabled(True)
        elif index == 2:
            self.flow.set_status("Shape, pace and the music bed.")
            self.flow.set_next_enabled(True)
        else:
            self.flow.set_status("")
            self.flow.set_next_enabled(count > 0)

    # -- shots -----------------------------------------------------------------------------
    def shots(self) -> list[Shot]:
        """In strip order, creating a default shot for anything new."""
        ordered: list[Shot] = []
        for path in self.strip.paths():
            key = str(path)
            shot = self._shots.get(key)
            if shot is None:
                shot = Shot(path=path, seconds=self.seconds_slider.value() / 10.0)
                self._shots[key] = shot
            ordered.append(shot)
        live = {str(path) for path in self.strip.paths()}
        for stale in [key for key in self._shots if key not in live]:
            del self._shots[stale]
        return ordered

    def current_shot(self) -> Shot | None:
        item = self.strip.currentItem()
        if item is None:
            return None
        return self._shots.get(item.data(Qt.ItemDataRole.UserRole))

    def _photos_changed(self) -> None:
        self.shots()
        self._refresh()

    def _selection_changed(self) -> None:
        self.inspector.set_shot(self.current_shot())

    def _shot_edited(self) -> None:
        self._refresh()

    def _analyse_current(self) -> None:
        shot = self.current_shot()
        if shot is None:
            return
        shot.analysis = sceneanalysis.analyse(shot.path)
        if shot.move in ("", "Auto"):
            shot.move = shot.analysis.move
        if shot.atmosphere in ("", "Auto", "None"):
            shot.atmosphere = shot.analysis.atmosphere
        if not shot.ai_prompt:
            shot.ai_prompt = shot.analysis.ai_prompt
        shot.motion_strength = shot.analysis.motion_strength
        self.inspector.set_shot(shot)
        self._refresh()

    # -- settings ---------------------------------------------------------------------------
    def settings(self) -> PhotoFilmSettings:
        return PhotoFilmSettings(
            aspect=self.aspect_box.currentText(),
            seconds_each=self.seconds_slider.value() / 10.0,
            crossfade=self.crossfade_slider.value() / 10.0,
            fps=self.fps_box.value(),
            fit=self.fit_box.currentText(),
            music=self._music,
            music_volume=self.music_volume.value() / 100.0,
            quality_crf=self.crf_box.value(),
        )

    def _seconds_changed(self) -> None:
        # Apply the global pace to shots the user has not set individually.
        value = self.seconds_slider.value() / 10.0
        for shot in self.shots():
            shot.seconds = value
        self._refresh()

    def _refresh(self) -> None:
        shots = self.shots()
        count = len(shots)

        max_blend = max(0, self.seconds_slider.value() - 2)
        if self.crossfade_slider.maximum() != max_blend:
            self.crossfade_slider.setMaximum(max_blend)
        if self.crossfade_slider.value() > max_blend:
            self.crossfade_slider.setValue(max_blend)

        self._refresh_ai_banner()

        settings = self.settings()
        self.seconds_label.setText(f"{settings.seconds_each:.1f} seconds per photo")
        self.crossfade_label.setText(f"Blend between photos: {settings.crossfade:.1f} s")
        self.count_label.setText(f"{count} photo{'s' if count != 1 else ''}")
        self.empty_hint.setVisible(count == 0)
        self._refresh_badges(shots)

        if count == 0:
            self.estimate_label.setText("Add photos to begin")
            self.cost_label.setText("")
            self.create_button.setEnabled(False)
            return

        total = settings.total_seconds(shots)
        width, height = settings.size
        self.estimate_label.setText(f"{total:.1f} seconds  ·  {width}×{height}")

        ai_count = sum(1 for shot in shots if shot.transform == "AI video")
        if ai_count:
            self.cost_label.setText(
                f"{ai_count} shot{'s' if ai_count != 1 else ''} will be sent to an "
                f"image-to-video model — that costs money and takes minutes each. "
                f"The other {count - ai_count} render locally and free."
            )
            self.cost_label.setObjectName("warnText")
        else:
            self.cost_label.setText("Everything renders locally and free.")
            self.cost_label.setObjectName("okText")

        self.create_button.setEnabled(self._job_id is None)

    def _go_from_banner(self, target: str) -> None:
        window = getattr(self, "_main_window", None)
        if window is not None:
            window.go_to(target)

    def _refresh_ai_banner(self) -> None:
        wants_ai = self.ai_policy.currentText() != "Never (camera moves only)"
        model, reason = autopilot.best_ai_model(self.context.registry)

        if model is None and wants_ai:
            # Saying only what is missing reads as "this app has no AI" when the local,
            # free AI is installed and working elsewhere. Name what runs now, then give
            # the two routes to the part that does not, as things to click.
            upscaler = repair_module.installed_upscaler(self.context.local_tools)
            have = []
            if upscaler:
                have.append("AI upscaling (Real-ESRGAN, installed)")
            if self.context.local_tools.get("rife") and                     self.context.local_tools.get("rife").installed:
                have.append("AI frame interpolation (RIFE)")
            working = (" Local AI that <i>is</i> working: " + ", ".join(have) + "."
                       if have else "")
            self.ai_banner.setText(
                "<b>No AI video <i>generator</i> is connected</b>, so each shot gets a "
                "free camera move — a 2D zoom or pan rather than the 3D movement a "
                "generator produces." + working
                + "<br>To turn a photo into generated motion you need one of: "
                  f"<a href='hub' style='color:{theme.ACCENT}'>run ComfyUI locally</a> "
                  f"(open weights, a large download) or "
                  f"<a href='settings' style='color:{theme.ACCENT}'>add a fal.ai or "
                  f"Replicate key</a> (paid per clip)."
            )
            self.ai_banner.setOpenExternalLinks(False)
            # The banner is refreshed on every model change; connecting each time would
            # open the target page once per refresh, and disconnecting an unconnected
            # signal makes Qt print a warning. So the link is wired once.
            if not getattr(self, "_banner_links_wired", False):
                self.ai_banner.linkActivated.connect(self._go_from_banner)
                self._banner_links_wired = True
            self.ai_banner.setStyleSheet(
                f"background:{theme.tint(theme.WARNING, 0.16)}; color:{theme.TEXT};"
                f"border:1px solid {theme.tint(theme.WARNING, 0.45)}; border-radius:8px; padding:10px;"
            )
            self.ai_banner.setVisible(True)
        elif model is not None and wants_ai:
            if model in autopilot.LOCAL_MODELS:
                minutes = autopilot.local_minutes_per_shot(self.settings().seconds_each)
                self.ai_banner.setText(
                    f"<b>Generated motion runs on this PC</b> with Wan 2.2 through ComfyUI — "
                    f"about {minutes:.0f} min per AI shot on this GPU. The other shots keep a "
                    f"camera move and render in seconds.")
            else:
                self.ai_banner.setText(f"AI video ready — {model[1]}. {reason}")
            self.ai_banner.setStyleSheet(
                f"background:{theme.tint(theme.FREE, 0.12)}; color:{theme.FREE};"
                f"border:1px solid {theme.tint(theme.FREE, 0.35)}; border-radius:8px; padding:10px;"
            )
            self.ai_banner.setVisible(True)
        else:
            self.ai_banner.setVisible(False)

    def _refresh_badges(self, shots: list[Shot]) -> None:
        """Each thumbnail says how it will be animated."""
        for row in range(self.strip.count()):
            item = self.strip.item(row)
            path = item.data(Qt.ItemDataRole.UserRole)
            shot = self._shots.get(path)
            if shot is None:
                continue
            badge, _ = TRANSFORM_BADGE.get(shot.transform, ("", ""))
            move = shot.move if shot.move != "Auto" else (
                shot.analysis.move if shot.analysis else "auto"
            )
            item.setText(f"{row + 1}. {shot.path.name}\n{badge} · {move}")
            item.setToolTip(
                f"{shot.path}\n{shot.transform} · {move} · {shot.seconds:.1f}s\n"
                f"Atmosphere: {shot.atmosphere}"
            )

    # -- actions ------------------------------------------------------------------------------
    def add_photos(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Choose photos", "", PHOTO_FILTER)
        if paths:
            added = self.strip.add_paths([Path(path) for path in paths])
            self.status.emit(f"Added {added} photo(s)")

    def add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder of photos")
        if not folder:
            return
        paths = [path for path in sorted(Path(folder).rglob("*"))
                 if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES]
        if not paths:
            QMessageBox.information(self, "No photos", "That folder has no readable photos.")
            return
        self.strip.add_paths(paths)
        self.status.emit(f"Added {len(paths)} photo(s)")

    def choose_music(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose music", "", "Audio (*.mp3 *.wav *.m4a *.aac *.flac *.ogg)"
        )
        if path:
            self._music = Path(path)
            self.music_label.setText(self._music.name)

    def clear_music(self) -> None:
        self._music = None
        self.music_label.setText("None")

    def apply_globals(self) -> None:
        shots = self.shots()
        if not shots:
            return
        transform = self.global_transform.currentText()
        move = self.global_move.currentText()
        atmosphere = self.global_atmosphere.currentText()
        changed: list[str] = []
        for shot in shots:
            if transform != UNCHANGED:
                shot.transform = transform
                shot.resolved_transform = ""
            if move != UNCHANGED:
                shot.move = move if move != "Auto" else (
                    shot.analysis.move if shot.analysis else "Auto"
                )
            if atmosphere != UNCHANGED:
                shot.atmosphere = atmosphere if atmosphere != "Auto" else (
                    shot.analysis.atmosphere if shot.analysis else "None"
                )
        for label, value in (("animation", transform), ("move", move),
                             ("atmosphere", atmosphere)):
            if value != UNCHANGED:
                changed.append(f"{label} → {value}")

        store = self.context.learning
        for shot in shots:
            scene = getattr(shot.analysis, "scene", "") if shot.analysis else ""
            if transform != UNCHANGED:
                store.record("transform", shot.transform, scene=scene, changed=True,
                             project=self.context.project_key)
            if move != UNCHANGED:
                store.record("move", shot.move, scene=scene, changed=True,
                             project=self.context.project_key)
            if atmosphere != UNCHANGED:
                store.record("atmosphere", shot.atmosphere, scene=scene, changed=True,
                             project=self.context.project_key)

        self.inspector.set_shot(self.current_shot())
        self._refresh()
        self.status.emit(
            f"Set on all {len(shots)} photos: " + ", ".join(changed) if changed
            else "Nothing to apply — every dropdown is on “Don’t change”."
        )

    def run_autopilot(self) -> None:
        paths = self.strip.paths()
        if not paths:
            QMessageBox.information(self, "No photos", "Add some photos first.")
            return

        policy = {"Where it helps": "auto", "Every photo": "always",
                  "Never (camera moves only)": "never"}[self.ai_policy.currentText()]
        target = float(self.target_length.value())
        reorder = self.reorder_check.isChecked()
        context = self.context

        def work(job_context):
            return autopilot.plan(
                paths, context.registry,
                base_settings=self.settings(),
                target_seconds=target,
                use_ai=policy,
                reorder=reorder,
                learning=context.learning,
                progress=lambda fraction, message: job_context.progress(fraction, message),
            )

        job = self.context.jobs.submit(
            f"Analysing {len(paths)} photos", work, category="autopilot"
        )
        self._autopilot_job = job.id
        self.status.emit(f"Looking at {len(paths)} photos")
        self.auto_button.setEnabled(False)
        self.auto_button.setText("Thinking...")

    def apply_plan(self, plan: "autopilot.AutoPlan") -> None:
        """Takes the autopilot's answer and puts it into the UI."""
        self._shots = {str(shot.path): shot for shot in plan.shots}

        # The plan may have reordered; rebuild the strip to match.
        self.strip.blockSignals(True)
        self.strip.clear()
        self.strip.add_paths([shot.path for shot in plan.shots])
        self.strip.blockSignals(False)

        settings = plan.settings
        self.aspect_box.setCurrentText(settings.aspect)
        self.fit_box.setCurrentText(settings.fit)
        self.seconds_slider.blockSignals(True)
        self.seconds_slider.setValue(int(round(settings.seconds_each * 10)))
        self.seconds_slider.blockSignals(False)
        self.crossfade_slider.setMaximum(max(0, self.seconds_slider.value() - 2))
        self.crossfade_slider.setValue(
            min(int(round(settings.crossfade * 10)), self.crossfade_slider.maximum())
        )

        self._plan_report = plan.report()
        self.report_box.setPlainText(self._plan_report)
        self.tabs.setCurrentIndex(2)

        self.auto_button.setEnabled(True)
        self.auto_button.setText("Decide everything for me")
        self._refresh()
        if self.strip.count():
            self.strip.setCurrentRow(0)
        self.status.emit("Auto finished — see the Reasoning tab")

    def autopilot_failed(self, message: str) -> None:
        self.auto_button.setEnabled(True)
        self.auto_button.setText("Decide everything for me")
        QMessageBox.warning(self, "Auto could not finish", message[:1000])

    # -- render ---------------------------------------------------------------------------------
    def create(self) -> None:
        # The progress bar and the result live on the last step. Starting a
        # render from an earlier step and leaving the user there means the
        # work happens somewhere they cannot see.
        if hasattr(self, "flow"):
            self.flow.go_to(len(self.flow.steps) - 1)
        shots = self.shots()
        if not shots:
            return
        if not self.context.locator.available:
            QMessageBox.warning(
                self, "ffmpeg needed",
                "This needs ffmpeg, which is not installed.\n\nOpen Settings and press "
                "'Install ffmpeg'.",
            )
            return
        if self.context.project is None:
            QMessageBox.information(self, "No project", "Create or open a project first.")
            return

        project = self.context.require_project()
        settings = self.settings()
        destination = project.renders_dir / "photo_clip.mp4"
        counter = 1
        while destination.exists():
            destination = project.renders_dir / f"photo_clip_{counter}.mp4"
            counter += 1

        work_dir = project.root / "cache" / "photofilm"
        locator = self.context.locator
        context = self.context
        renderer = self._make_ai_renderer()

        def work(job_context):
            result = photofilm.render(
                locator, shots, destination, settings, work_dir,
                progress=lambda fraction, message: job_context.progress(fraction, message),
                should_cancel=lambda: job_context.cancelled,
                ai_renderer=renderer,
            )
            context.catalogue(result, source="photofilm", label="Photo clip")

            # Rendering is the moment a plan becomes a decision worth learning from.
            store = context.learning
            learning.observe_plan(store, shots, project=context.project_key)
            store.record("aspect", settings.aspect, changed=False,
                         project=context.project_key)
            store.record("seconds_each", f"{settings.seconds_each:.2f}", changed=False,
                         project=context.project_key)
            store.record("fit", settings.fit, changed=False, project=context.project_key)
            if settings.music:
                store.record("music_volume", f"{settings.music_volume:.2f}",
                             changed=False, project=context.project_key)
            store.record_render(
                project=context.project_key, shots=len(shots),
                seconds=settings.total_seconds(shots), aspect=settings.aspect,
                fps=settings.fps, crossfade=settings.crossfade,
                used_ai=any(shot.resolved_transform == "AI video" for shot in shots),
                detail={"moves": [shot.move for shot in shots],
                        "atmospheres": [shot.atmosphere for shot in shots]},
            )
            return result

        job = self.context.jobs.submit(
            f"Photo clip: {len(shots)} photos", work, category="photofilm"
        )
        self._job_id = job.id
        self._set_running(True)
        self.status.emit(f"Rendering {len(shots)} photos into a clip")

    def _make_ai_renderer(self):
        """Bridges a Shot to the provider registry, or None when nothing can run."""
        context = self.context

        def renderer(shot: Shot, index: int, output_dir: Path, progress, cancel):
            provider_key = shot.ai_provider or "catalog"
            model_id = shot.ai_model
            if not model_id:
                chosen, _ = autopilot.best_ai_model(context.registry)
                if chosen is None:
                    return None
                provider_key, model_id = chosen

            provider = context.registry.get(provider_key)
            if provider is None:
                return None

            width, height = self.settings().size
            aspect = "1:1" if width == height else ("9:16" if height > width else "16:9")
            request = GenerationRequest(
                capability=Capability.IMAGE_TO_VIDEO,
                model_id=model_id,
                prompt=shot.ai_prompt or "",
                reference_images=[shot.path],
                options={
                    "duration": max(2, int(round(shot.seconds))),
                    "duration_seconds": max(2, int(round(shot.seconds))),
                    "seconds": shot.seconds,
                    "aspect_ratio": aspect,
                },
                output_dir=output_dir,
            )
            result = provider.generate(
                request, progress=progress, should_cancel=cancel or (lambda: False)
            )
            # A model that animates the scene but not the camera says so; the film then
            # adds the shot's own camera move when it conforms the clip.
            shot.ai_static_camera = (result.raw or {}).get("camera") == "static"
            return result.files[0] if result.files else None

        return renderer

    def cancel(self) -> None:
        if self._job_id is not None:
            self.context.jobs.cancel(self._job_id)
            self.progress_label.setText("Cancelling...")

    def _set_running(self, running: bool) -> None:
        self.create_button.setEnabled(not running)
        self.create_button.setText("Rendering..." if running else "Create video")
        self.progress.setVisible(running)
        self.cancel_button.setVisible(running)
        if running:
            self.progress.setValue(0)

    def on_job_update(self, job) -> None:
        if job.category == "autopilot":
            if job.state.value == "done" and job.result is not None:
                self.apply_plan(job.result)
            elif job.state.value in ("failed", "cancelled"):
                self.autopilot_failed(job.error or "Cancelled")
            return

        if job.id != self._job_id:
            return
        self.progress.setValue(int(job.progress * 100))
        self.progress_label.setText(job.message)
        if job.state.value not in ("done", "failed", "cancelled"):
            return

        self._job_id = None
        self._set_running(False)

        if job.state.value == "done":
            self.progress_label.setText("Finished")
            if isinstance(job.result, Path):
                self._result = job.result
                self.preview.show_path(job.result)
                self.save_button.setEnabled(True)
                self.folder_button.setEnabled(True)
                self.library_changed.emit()
            notes = [f"{shot.path.name}: {shot.note}" for shot in self.shots() if shot.note]
            if notes:
                self.report_box.setPlainText(
                    self._plan_report + "\n\nDuring the render:\n  " + "\n  ".join(notes)
                )
        elif job.state.value == "failed":
            first_line = (job.error.splitlines() or ["Unknown error"])[0]
            self.progress_label.setText(f"Failed: {first_line[:160]}")
            QMessageBox.warning(self, "Could not make the clip",
                                job.error[:1200] or "The render failed without a message.")
        else:
            self.progress_label.setText("Cancelled")

        self._refresh()

    def save_copy(self) -> None:
        if not self._result or not self._result.exists():
            return
        target, _ = QFileDialog.getSaveFileName(
            self, "Save the video", str(Path.home() / self._result.name), "Video (*.mp4)"
        )
        if target:
            import shutil
            shutil.copy2(self._result, target)
            self.status.emit(f"Saved to {target}")

    def show_in_folder(self) -> None:
        if self._result and self._result.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._result.parent)))
