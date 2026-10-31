"""Reusable widgets: the declarative options form, the media preview, and the job bridge."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QSplitter,
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QSizePolicy, QSlider, QSpinBox, QStackedWidget,
    QTextEdit, QVBoxLayout, QWidget,
)

from ..jobs import Job
from . import theme

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
            widget.setPlaceholderText('{"key": "value"} -- merged into the request payload')
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
        self._play_button = QPushButton("Play")
        self._play_button.clicked.connect(self.toggle_play)
        self._scrubber = QSlider(Qt.Orientation.Horizontal)
        self._scrubber.sliderMoved.connect(self._seek)
        self._time_label = QLabel("00:00 / 00:00")
        self._open_button = QPushButton("Open externally")
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
            self._play_button.setText("Play")
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
            self._play_button.setText("Play")
        else:
            self._player.play()
            self._play_button.setText("Pause")

    def stop(self) -> None:
        if self._player:
            self._player.stop()
            self._play_button.setText("Play")

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
