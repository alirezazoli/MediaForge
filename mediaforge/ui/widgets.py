"""Reusable widgets: the declarative options form, the media preview, and the job bridge."""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QSplitter,
    QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame,
    QGridLayout, QHBoxLayout, QLabel, QLayout, QLineEdit, QPushButton, QSizePolicy, QSlider,
    QSpinBox, QStackedWidget, QTextEdit, QToolButton, QVBoxLayout, QWidget,
)

from ..jobs import Job
from . import icons, theme

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PySide6.QtMultimediaWidgets import QVideoWidget
    MULTIMEDIA_AVAILABLE = True
except ImportError:  # pragma: no cover - Qt build without multimedia
    MULTIMEDIA_AVAILABLE = False


class JobBridge(QObject):
    """Carries job updates from worker threads onto the UI thread.

    Qt signals are thread-safe and queue automatically across threads, which is exactly
    what the JobQueue listener contract needs.
    """

    job_changed = Signal(object)

    def listener(self, job: Job) -> None:
        self.job_changed.emit(job)


class ResponsiveSplitter(QSplitter):
    """A splitter that drops panes instead of letting them overlap.

    Qt does not shrink a child below its minimum width. When the minimums add up to more
    than the splitter has, it hands each child less than it needs and the child paints
    over its neighbour -- which is exactly what "the windows go inside each other" looks
    like. Rather than pick smaller minimums and hope, this hides panes from the right
    until what is left genuinely fits, and brings them back as the window grows.

    Panes are dropped right-to-left because in every panel here the rightmost column is
    the preview: the one a person can most afford to lose on a narrow window.
    """

    panes_changed = Signal(int)

    def __init__(self, orientation=Qt.Orientation.Horizontal, parent=None) -> None:
        super().__init__(orientation, parent)
        self.setChildrenCollapsible(False)
        self.setHandleWidth(12)
        self._optional_from = 1        # index of the first pane that may be hidden

    def set_optional_from(self, index: int) -> None:
        """Panes at this index and beyond may be hidden when space runs out."""
        self._optional_from = max(1, index)

    def _fit(self) -> None:
        available = self.width() - self.handleWidth() * max(0, self.count() - 1)
        if available <= 0:
            return
        visible = [i for i in range(self.count()) if not self.widget(i).isHidden()]
        needed = sum(self.widget(i).minimumWidth() for i in visible)

        # too tight: drop optional panes from the right
        while needed > available and len(visible) > self._optional_from:
            index = visible.pop()
            self.widget(index).hide()
            needed -= self.widget(index).minimumWidth()
        # room again: bring them back in the order they were dropped
        for index in range(self.count()):
            widget = self.widget(index)
            if widget.isHidden():
                if needed + widget.minimumWidth() <= available:
                    widget.show()
                    needed += widget.minimumWidth()
                else:
                    break
        self.panes_changed.emit(sum(
            1 for i in range(self.count()) if not self.widget(i).isHidden()))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit()


class OptionsForm(QWidget):
    """Builds editors from a ModelSpec's declarative option schema."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QFormLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self._editors: dict[str, QWidget] = {}

    def rebuild(self, schema: dict[str, dict[str, Any]]) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._editors.clear()

        for name, spec in (schema or {}).items():
            label = spec.get("label") or name.replace("_", " ").capitalize()
            editor = self._build_editor(spec)
            self._editors[name] = editor
            self._layout.addRow(label, editor)

    def _build_editor(self, spec: dict[str, Any]) -> QWidget:
        kind = spec.get("type", "str")
        default = spec.get("default")

        if kind == "choice":
            widget = QComboBox()
            widget.addItems([str(choice) for choice in spec.get("choices", [])])
            if default is not None:
                index = widget.findText(str(default))
                if index >= 0:
                    widget.setCurrentIndex(index)
            return widget
        if kind == "int":
            widget = QSpinBox()
            widget.setRange(int(spec.get("min", 0)), int(spec.get("max", 1_000_000)))
            widget.setValue(int(default if default is not None else 0))
            return widget
        if kind == "float":
            widget = QDoubleSpinBox()
            widget.setDecimals(2)
            widget.setSingleStep(0.05)
            widget.setRange(float(spec.get("min", 0.0)), float(spec.get("max", 100.0)))
            widget.setValue(float(default if default is not None else 0.0))
            return widget
        if kind == "bool":
            widget = QCheckBox()
            widget.setChecked(bool(default))
            return widget
        if kind == "file":
            return FilePicker(str(default or ""))
        if kind == "text":
            widget = QTextEdit()
            widget.setPlainText(str(default or ""))
            widget.setMaximumHeight(90)
            json_field = "json" in str(spec.get("label", "")).lower()
            widget.setPlaceholderText(spec.get("placeholder") or (
                '{"key": "value"} -- merged into the request payload' if json_field else ""))
            return widget

        widget = QLineEdit(str(default or ""))
        return widget

    def values(self) -> dict[str, Any]:
        collected: dict[str, Any] = {}
        for name, editor in self._editors.items():
            if isinstance(editor, QComboBox):
                collected[name] = editor.currentText()
            elif isinstance(editor, QSpinBox):
                collected[name] = editor.value()
            elif isinstance(editor, QDoubleSpinBox):
                collected[name] = editor.value()
            elif isinstance(editor, QCheckBox):
                collected[name] = editor.isChecked()
            elif isinstance(editor, FilePicker):
                collected[name] = editor.path()
            elif isinstance(editor, QTextEdit):
                collected[name] = editor.toPlainText()
            elif isinstance(editor, QLineEdit):
                collected[name] = editor.text()
        return collected


class FilePicker(QWidget):
    def __init__(self, initial: str = "", filter_spec: str = "All files (*.*)") -> None:
        super().__init__()
        self._filter = filter_spec
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._edit = QLineEdit(initial)
        button = QPushButton("Browse")
        button.clicked.connect(self._browse)
        layout.addWidget(self._edit, 1)
        layout.addWidget(button)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select file", self._edit.text(), self._filter)
        if path:
            self._edit.setText(path)

    def path(self) -> str:
        return self._edit.text().strip()

    def set_path(self, value: str) -> None:
        self._edit.setText(value)


class MediaPreview(QWidget):
    """Shows a still for images and a real player for video/audio."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._stack = QStackedWidget()
        self._image_label = QLabel("No preview")
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setMinimumHeight(220)
        self._restyle_placeholder()
        self._stack.addWidget(self._image_label)

        self._player = None
        self._video_widget = None
        if MULTIMEDIA_AVAILABLE:
            self._video_widget = QVideoWidget()
            self._video_widget.setMinimumHeight(220)
            self._stack.addWidget(self._video_widget)
            self._player = QMediaPlayer()
            self._audio = QAudioOutput()
            self._audio.setVolume(0.8)
            self._player.setAudioOutput(self._audio)
            self._player.setVideoOutput(self._video_widget)
            self._player.positionChanged.connect(self._on_position)
            self._player.durationChanged.connect(self._on_duration)

        layout.addWidget(self._stack, 1)

        controls = QHBoxLayout()
        self._play_button = QPushButton()
        self._play_button.setObjectName("iconButton")
        self._play_button.setToolTip("Play / pause (Space)")
        icons.button(self._play_button, "play", "@ACCENT", 18)
        self._play_button.clicked.connect(self.toggle_play)
        self._scrubber = QSlider(Qt.Orientation.Horizontal)
        self._scrubber.sliderMoved.connect(self._seek)
        self._time_label = QLabel("00:00 / 00:00")
        self._open_button = QPushButton()
        self._open_button.setObjectName("iconButton")
        self._open_button.setToolTip("Open in the system player")
        icons.button(self._open_button, "open", None, 16)
        self._open_button.clicked.connect(self._open_external)
        for widget in (self._play_button, self._scrubber, self._time_label, self._open_button):
            controls.addWidget(widget, 1 if widget is self._scrubber else 0)
        layout.addLayout(controls)

        self._current: Path | None = None
        self._set_controls_enabled(False)

    def _restyle_placeholder(self) -> None:
        self._image_label.setStyleSheet(
            f"background:{theme.SURFACE_2}; color:{theme.TEXT_FAINT}; border-radius:6px;")

    def restyle(self) -> None:
        """Called on a theme switch: the placeholder's colours are inline, so they have
        to be rebuilt or the preview stays the old theme's black rectangle."""
        self._restyle_placeholder()

    def _set_controls_enabled(self, enabled: bool) -> None:
        self._play_button.setEnabled(enabled)
        self._scrubber.setEnabled(enabled)

    def show_path(self, path: Path | None) -> None:
        self._current = path
        if self._player:
            self._player.stop()
        if not path or not path.exists():
            self._image_label.setText("No preview")
            self._image_label.setPixmap(QPixmap())
            self._stack.setCurrentWidget(self._image_label)
            self._set_controls_enabled(False)
            return

        suffix = path.suffix.lower()
        is_image = suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
        if is_image:
            pixmap = QPixmap(str(path))
            if pixmap.isNull():
                self._image_label.setText(f"Cannot preview {path.name}")
            else:
                self._image_label.setPixmap(
                    pixmap.scaled(self._image_label.size() * 0.98,
                                  Qt.AspectRatioMode.KeepAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation)
                )
                self._image_label.setText("")
            self._stack.setCurrentWidget(self._image_label)
            self._set_controls_enabled(False)
            return

        if self._player and self._video_widget:
            self._stack.setCurrentWidget(self._video_widget)
            self._player.setSource(QUrl.fromLocalFile(str(path)))
            self._set_controls_enabled(True)
            icons.button(self._play_button, "play", "@ACCENT", 18)
        else:
            self._image_label.setText(
                f"{path.name}\n\nQt Multimedia is unavailable -- use 'Open externally'."
            )
            self._stack.setCurrentWidget(self._image_label)
            self._set_controls_enabled(False)

    def toggle_play(self) -> None:
        if not self._player:
            return
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
            icons.button(self._play_button, "play", "@ACCENT", 18)
        else:
            self._player.play()
            icons.button(self._play_button, "pause", "@ACCENT", 18)

    def stop(self) -> None:
        if self._player:
            self._player.stop()
            icons.button(self._play_button, "play", "@ACCENT", 18)

    def _seek(self, value: int) -> None:
        if self._player:
            self._player.setPosition(value)

    def _on_position(self, position: int) -> None:
        if not self._scrubber.isSliderDown():
            self._scrubber.setValue(position)
        self._time_label.setText(
            f"{_ms(position)} / {_ms(self._player.duration() if self._player else 0)}"
        )

    def _on_duration(self, duration: int) -> None:
        self._scrubber.setRange(0, max(0, duration))

    def _open_external(self) -> None:
        if self._current and self._current.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._current)))


def _ms(value: int) -> str:
    seconds = value // 1000
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


class Card(QFrame):
    """A titled section box -- used everywhere to break panels into readable blocks."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setObjectName("card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 12)
        heading = QLabel(title)
        heading.setObjectName("cardTitle")
        outer.addWidget(heading)
        self.body = QVBoxLayout()
        self.body.setSpacing(8)
        outer.addLayout(self.body)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)


def centred_page(inner: QWidget, max_width: int = 980) -> QWidget:
    """One measure for every step page: centred and capped.

    On a wide screen an uncapped form puts a label and its control at opposite ends of
    the window, and the eye loses the row in between.
    """
    page = QWidget()
    layout = QHBoxLayout(page)
    layout.setContentsMargins(theme.SPACE_5, theme.SPACE_4, theme.SPACE_5, theme.SPACE_4)
    layout.addStretch(1)
    inner.setMaximumWidth(max_width)
    layout.addWidget(inner, 6)
    layout.addStretch(1)
    return page


COMBO_MIN_WIDTH = 160
LONG_LABEL_CHARACTERS = 60


def let_text_shrink(root: QWidget) -> None:
    """Lets text-heavy widgets narrow instead of holding the whole window wide.

    A dropdown asks for the width of its longest entry -- a model name with its provider
    and status note runs to 750px -- and a label that does not wrap asks for its whole
    sentence on one line. Qt adds those up into a minimum the window cannot go below, and
    on a laptop screen it squeezes them over each other instead. Applied once to the
    finished window, like the button icons.
    """
    for combo in root.findChildren(QComboBox):
        combo.setMinimumWidth(min(combo.minimumSizeHint().width(), COMBO_MIN_WIDTH))
        combo.view().setMinimumWidth(combo.view().sizeHintForColumn(0) + theme.SPACE_5)
    for label in root.findChildren(QLabel):
        if not label.wordWrap() and len(label.text()) > LONG_LABEL_CHARACTERS:
            label.setWordWrap(True)


def show_in_stack(stack: QStackedWidget, widget: QWidget) -> None:
    """Shows one page and sizes the stack to it, not to the tallest page it holds."""
    for index in range(stack.count()):
        page = stack.widget(index)
        policy = QSizePolicy.Policy.Preferred if page is widget else QSizePolicy.Policy.Ignored
        page.setSizePolicy(policy, policy)
    stack.setCurrentWidget(widget)
    stack.updateGeometry()


class TileGrid(QWidget):
    """A choice shown as icon tiles in labelled groups.

    A forty-row dropdown hides every option but one, so choosing means reading the whole
    list in a popup. Tiles show the options all at once, grouped, with an icon each --
    the eye finds "Crop" by its shape before reading a word. Columns follow the width,
    so a narrow window gets fewer, wider tiles rather than clipped labels.

    groups: [(heading, [(key, label, glyph, tooltip)])]
    """

    chosen = Signal(str)
    TILE_WIDTH = 210

    def __init__(self, groups: list[tuple[str, list[tuple[str, str, str, str]]]],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._tiles: dict[str, QToolButton] = {}
        self._sections: list[tuple[QLabel | None, QGridLayout, list[QToolButton]]] = []
        self._hidden: set[str] = set()
        self._columns = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.SPACE_2)
        # Without this the layout pins the grid's minimum to however many columns it last
        # had, and a narrower window squeezes the tiles instead of reflowing them.
        layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        for heading, entries in groups:
            title = None
            if heading:
                title = QLabel(heading)
                title.setObjectName("cardTitle")
                layout.addWidget(title)
            grid = QGridLayout()
            grid.setHorizontalSpacing(theme.SPACE_2)
            grid.setVerticalSpacing(theme.SPACE_2)
            tiles = []
            for key, label, glyph, tip in entries:
                # Parented from the start: a parentless widget that is made visible is a
                # window, and each tile used to flash up on its own while the app started.
                tile = QToolButton(self)
                tile.setObjectName("tile")
                tile.setText(label)
                tile.setToolTip(tip or label)
                tile.setCheckable(True)
                tile.setCursor(Qt.CursorShape.PointingHandCursor)
                tile.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
                tile.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                icons.button(tile, glyph, "@ACCENT", 18)
                tile.clicked.connect(lambda _=False, k=key: self.chosen.emit(k))
                self._group.addButton(tile)
                self._tiles[key] = tile
                tiles.append(tile)
            self._sections.append((title, grid, tiles))
            layout.addLayout(grid)
            layout.addSpacing(theme.SPACE_2)
        self._reflow(3)

    def _reflow(self, columns: int, force: bool = False) -> None:
        if columns == self._columns and not force:
            return
        self._columns = columns
        for title, grid, tiles in self._sections:
            for tile in tiles:
                grid.removeWidget(tile)
            shown = [tile for key, tile in self._tiles.items()
                     if tile in tiles and key not in self._hidden]
            for index, tile in enumerate(shown):
                grid.addWidget(tile, index // columns, index % columns)
            for tile in tiles:
                tile.setVisible(tile in shown)
            for column in range(6):
                grid.setColumnStretch(column, 1 if column < columns else 0)
            if title is not None:
                title.setVisible(bool(shown))
        self.updateGeometry()

    def set_available(self, keys) -> None:
        """Shows only these tiles; a group left empty loses its heading too."""
        available = set(keys)
        self._hidden = set(self._tiles) - available
        self._reflow(self._columns or 3, force=True)

    def keys(self) -> list[str]:
        return [key for key in self._tiles if key not in self._hidden]

    def _tile_width(self) -> int:
        """The widest label decides the column width; a fixed guess squeezed long ones."""
        widths = [tile.sizeHint().width() for key, tile in self._tiles.items()
                  if key not in self._hidden]
        return max([self.TILE_WIDTH] + widths)

    def minimumSizeHint(self) -> QSize:
        # One column is always possible, so that is all the grid asks of its parent; the
        # columns it shows follow the width it is actually given.
        return QSize(self._tile_width(), super().minimumSizeHint().height())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        spacing = theme.SPACE_2
        self._reflow(max(1, min(5, (self.width() + spacing) // (self._tile_width() + spacing))))

    def select(self, key: str) -> None:
        tile = self._tiles.get(key)
        if tile is not None:
            tile.setChecked(True)

    def current(self) -> str:
        return next((key for key, tile in self._tiles.items() if tile.isChecked()), "")

    def label(self, key: str) -> str:
        tile = self._tiles.get(key)
        return tile.text() if tile else key


class JobStatusCard(QFrame):
    """Progress, outcome and next moves for the job a step flow just started.

    Sending the user to a separate Jobs screen to find out whether their trim worked
    breaks the flow they are in. The job reports here, on the step that started it: a
    bar while it runs, the error in plain words if it fails, and what to do next once
    it is done.
    """

    cancel_requested = Signal(int)

    def __init__(self, title: str = "Output", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.job_id: int | None = None
        self.result_path: Path | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(theme.SPACE_4, theme.SPACE_3, theme.SPACE_4, theme.SPACE_4)
        outer.setSpacing(theme.SPACE_2)
        heading = QLabel(title)
        heading.setObjectName("cardTitle")
        outer.addWidget(heading)

        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.RichText)
        outer.addWidget(self.summary)

        from PySide6.QtWidgets import QProgressBar
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(4)
        self.bar.hide()
        outer.addWidget(self.bar)

        self.message = QLabel("")
        self.message.setObjectName("subtle")
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.TextFormat.RichText)
        self.message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(self.message)

        self.actions = QHBoxLayout()
        self.actions.setSpacing(theme.SPACE_2)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("ghost")
        icons.button(self.cancel_button, "stop", "@ACCENT")
        self.cancel_button.clicked.connect(
            lambda: self.job_id is not None and self.cancel_requested.emit(self.job_id))
        self.cancel_button.hide()
        self.actions.addWidget(self.cancel_button)
        self.folder_button = QPushButton("Show in folder")
        self.folder_button.setObjectName("ghost")
        icons.button(self.folder_button, "folder", "@ACCENT")
        self.folder_button.clicked.connect(self._reveal)
        self.folder_button.hide()
        self.actions.addWidget(self.folder_button)
        self.actions.addStretch(1)
        outer.addLayout(self.actions)

    def add_action(self, button: QPushButton) -> None:
        """A next move offered once the job is done (hidden until then)."""
        button.hide()
        button.setProperty("onDone", True)
        self.actions.insertWidget(self.actions.count() - 1, button)

    def _done_actions(self, visible: bool) -> None:
        for index in range(self.actions.count()):
            widget = self.actions.itemAt(index).widget()
            if widget is not None and widget.property("onDone"):
                widget.setVisible(visible)

    def show_ready(self, text: str) -> None:
        """Before anything runs: what pressing the primary button will do."""
        self.job_id = None
        self.summary.setText(text)
        self.bar.hide()
        self.message.setText("")
        self.cancel_button.hide()
        self.folder_button.hide()
        self._done_actions(False)

    def track(self, job_id: int, text: str) -> None:
        self.job_id = job_id
        self.result_path = None
        self.summary.setText(text)
        self.bar.setValue(0)
        self.bar.show()
        self.message.setText("Starting…")
        self.cancel_button.show()
        self.folder_button.hide()
        self._done_actions(False)

    def show_job(self, job: Job) -> bool:
        """Shows a job's progress if it is the one being tracked. Returns whether it was."""
        from ..jobs import JobState

        if job.id != self.job_id:
            return False
        if job.state in (JobState.QUEUED, JobState.RUNNING):
            self.bar.setValue(int(job.progress * 100))
            self.message.setText(html.escape(job.message or "Working…"))
            return True
        self.bar.hide()
        self.cancel_button.hide()
        if job.state == JobState.DONE:
            result = job.result
            self.result_path = result if isinstance(result, Path) else None
            if self.result_path is not None:
                self.message.setText(
                    f"<span style='color:{theme.SUCCESS}'>Done.</span>  "
                    f"{html.escape(self.result_path.name)}")
            else:
                self.message.setText(
                    f"<span style='color:{theme.SUCCESS}'>Done.</span>  "
                    f"{html.escape(str(result or '')[:600])}")
            self.folder_button.setVisible(self.result_path is not None)
            self._done_actions(True)
        elif job.state == JobState.CANCELLED:
            self.message.setText("Cancelled. Nothing was written.")
        else:
            first = (job.error or "Unknown error").strip().splitlines()
            self.message.setText(
                f"<span style='color:{theme.DANGER}'>Failed.</span>  "
                f"{html.escape((first[-1] if first else '')[:400])}")
        return True

    def _reveal(self) -> None:
        if self.result_path is None:
            return
        target = self.result_path if self.result_path.is_dir() else self.result_path.parent
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))


def message_row(text: str, on_click: Callable[[], None] | None = None) -> QWidget:
    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    label = QLabel(text)
    label.setWordWrap(True)
    layout.addWidget(label, 1)
    if on_click:
        button = QPushButton("Fix")
        button.clicked.connect(on_click)
        layout.addWidget(button)
    return container
