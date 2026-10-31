"""Before/after comparison with a draggable wipe.

Two things make this more than a pair of thumbnails side by side:

  * **The two files no longer share a clock.** Cutting eight seconds of dead air shifts
    everything after it, so the same wall-clock second in each file shows unrelated
    frames. Every position is mapped through the cut list, and a moment that was removed
    says so instead of silently showing whatever landed there.

  * **The same pixels have to be under the same part of the screen.** An upscale changes
    the resolution, so both frames are drawn into one identical rectangle and the wipe
    cuts across that -- otherwise "sharper" and "bigger" become impossible to tell apart.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QSizePolicy, QSlider, QVBoxLayout, QWidget,
)

from .. import ffmpeg_tools, repair
from . import theme

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def grab_frame(locator, source: Path, at: float, width: int = 960) -> QPixmap | None:
    """One frame as a pixmap. Returns None rather than raising -- this is a preview."""
    if not locator.available or not source.exists():
        return None
    target = Path(tempfile.gettempdir()) / f"mf_cmp_{abs(hash((str(source), round(at, 2))))}.png"
    if not target.exists():
        result = subprocess.run(
            [locator.ffmpeg, "-y", "-v", "error", "-ss", f"{max(0.0, at):.3f}",
             "-i", str(source), "-frames:v", "1", "-vf", f"scale={width}:-2", str(target)],
            capture_output=True, creationflags=_NO_WINDOW,
        )
        if result.returncode != 0 or not target.exists():
            return None
    pixmap = QPixmap(str(target))
    return None if pixmap.isNull() else pixmap


class WipeView(QWidget):
    """Draws two images in one rectangle, split by a draggable vertical line."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.before: QPixmap | None = None
        self.after: QPixmap | None = None
        self.split = 0.5
        self.message = "Repair a video to compare it with the original."
        self.setMinimumHeight(260)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.SplitHCursor)
        self.setMouseTracking(True)

    def set_frames(self, before: QPixmap | None, after: QPixmap | None,
                   message: str = "") -> None:
        self.before, self.after, self.message = before, after, message
        self.update()

    def _target_rect(self) -> QRect:
        """One rectangle for both images, using the BEFORE frame's aspect ratio.

        Fitting each image to its own size would make an upscaled 'after' draw larger,
        and the comparison would then be showing a size difference rather than a quality
        difference.
        """
        reference = self.before or self.after
        if reference is None or reference.isNull():
            return self.rect()
        area = self.rect().adjusted(2, 2, -2, -2)
        scaled = reference.size().scaled(area.size(), Qt.AspectRatioMode.KeepAspectRatio)
        return QRect(
            area.x() + (area.width() - scaled.width()) // 2,
            area.y() + (area.height() - scaled.height()) // 2,
            scaled.width(), scaled.height(),
        )

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(theme.SURFACE_2))
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        if self.before is None and self.after is None:
            painter.setPen(QColor(theme.TEXT_FAINT))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.message)
            return

        target = self._target_rect()
        boundary = target.x() + int(target.width() * self.split)

        if self.before is not None:
            painter.save()
            painter.setClipRect(QRect(target.x(), target.y(),
                                      boundary - target.x(), target.height()))
            painter.drawPixmap(target, self.before)
            painter.restore()
        if self.after is not None:
            painter.save()
            painter.setClipRect(QRect(boundary, target.y(),
                                      target.right() - boundary + 1, target.height()))
            painter.drawPixmap(target, self.after)
            painter.restore()

        painter.setPen(QPen(QColor(theme.ACCENT), 2))
        painter.drawLine(boundary, target.y(), boundary, target.bottom())

        painter.setPen(QColor(theme.TEXT))
        painter.drawText(target.adjusted(8, 6, -8, 0),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, "BEFORE")
        painter.drawText(target.adjusted(8, 6, -8, 0),
                         Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop, "AFTER")
        if self.message:
            painter.setPen(QColor(theme.WARNING))
            painter.drawText(target.adjusted(8, 0, -8, -6),
                             Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
                             self.message)

    def _set_from_x(self, x: int) -> None:
        target = self._target_rect()
        if target.width() <= 0:
            return
        self.split = min(1.0, max(0.0, (x - target.x()) / target.width()))
        self.update()

    def mousePressEvent(self, event) -> None:
        self._set_from_x(event.position().toPoint().x())

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._set_from_x(event.position().toPoint().x())


class ComparePanel(QWidget):
    """The wipe plus a scrub bar, wired to a source/output pair and a cut list."""

    status = Signal(str)

    def __init__(self, locator) -> None:
        super().__init__()
        self.locator = locator
        self.before_path: Path | None = None
        self.after_path: Path | None = None
        self.cuts: list[repair.Segment] = []
        self.duration = 0.0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.view = WipeView()
        layout.addWidget(self.view, 1)

        scrub_row = QHBoxLayout()
        self.time_slider = QSlider(Qt.Orientation.Horizontal)
        self.time_slider.setRange(0, 1000)
        self.time_slider.setEnabled(False)
        self.time_slider.valueChanged.connect(self._on_time_changed)
        self.time_label = QLabel("—")
        self.time_label.setMinimumWidth(190)
        scrub_row.addWidget(QLabel("Moment"))
        scrub_row.addWidget(self.time_slider, 1)
        scrub_row.addWidget(self.time_label)
        layout.addLayout(scrub_row)

        wipe_row = QHBoxLayout()
        self.wipe_slider = QSlider(Qt.Orientation.Horizontal)
        self.wipe_slider.setRange(0, 100)
        self.wipe_slider.setValue(50)
        self.wipe_slider.valueChanged.connect(self._on_wipe_changed)
        wipe_row.addWidget(QLabel("Wipe"))
        wipe_row.addWidget(self.wipe_slider, 1)
        layout.addLayout(wipe_row)

        self.caption = QLabel("")
        self.caption.setWordWrap(True)
        self.caption.setObjectName("subtle")
        layout.addWidget(self.caption)

    def set_pair(self, before: Path, after: Path, cuts: list[repair.Segment],
                 duration: float, note: str = "") -> None:
        self.before_path, self.after_path = Path(before), Path(after)
        self.cuts = list(cuts)
        self.duration = duration or 0.0
        self.time_slider.setEnabled(self.duration > 0)
        self.caption.setText(note)
        # Open on a moment that survived the cut, so the first thing shown is a real
        # comparison rather than "this moment was removed".
        first = repair.sample_times(self.duration, self.cuts, 1)
        self.time_slider.blockSignals(True)
        self.time_slider.setValue(
            int(1000 * (first[0] / self.duration)) if self.duration and first else 500
        )
        self.time_slider.blockSignals(False)
        self.refresh()

    def clear(self) -> None:
        self.before_path = self.after_path = None
        self.view.set_frames(None, None,
                             "Repair a video to compare it with the original.")
        self.time_slider.setEnabled(False)
        self.time_label.setText("—")
        self.caption.setText("")

    def current_source_time(self) -> float:
        return self.duration * self.time_slider.value() / 1000.0 if self.duration else 0.0

    def _on_wipe_changed(self, value: int) -> None:
        self.view.split = value / 100.0
        self.view.update()

    def _on_time_changed(self, _value: int) -> None:
        self.refresh()

    def refresh(self) -> None:
        if not self.before_path or not self.after_path:
            return
        at = self.current_source_time()
        mapped = repair.map_time(at, self.cuts, self.duration)
        before = grab_frame(self.locator, self.before_path, at)

        if mapped is None:
            # Deliberately still show the original: seeing exactly which frames were
            # thrown away is the point of scrubbing into a cut.
            self.view.set_frames(before, None,
                                 "This moment was cut — it is not in the repaired file.")
            self.time_label.setText(
                f"{ffmpeg_tools.format_timecode(at)}  →  removed"
            )
            return

        after = grab_frame(self.locator, self.after_path, mapped)
        self.view.set_frames(before, after, "" if after else "No frame at this point.")
        self.time_label.setText(
            f"{ffmpeg_tools.format_timecode(at)}  →  {ffmpeg_tools.format_timecode(mapped)}"
        )
