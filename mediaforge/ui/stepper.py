"""A step-by-step flow: one decision at a time, with a visible sense of place.

The three-column panels put every control on screen at once. That is defensible for a
tool you already know and hostile for one you do not: there is no first thing to do, no
sense of progress, and no way to tell which settings matter now and which are for later.

This is the alternative Google uses for a task with a beginning and an end -- a header
that shows where you are, one pane of content, and a bar at the bottom whose primary
button is always the next move. Steps stay clickable, because forcing someone forward
through a wizard they have already understood is its own kind of rude.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QStackedWidget,
    QVBoxLayout, QWidget,
)

from . import theme


@dataclass
class Step:
    key: str
    title: str
    hint: str = ""


class StepDot(QPushButton):
    """One entry in the header: a number, a name, and a state."""

    def __init__(self, index: int, step: Step) -> None:
        super().__init__()
        self.index = index
        self.step = step
        self.setObjectName("stepDot")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setText(f"{index + 1}   {step.title}")
        self.setToolTip(step.hint or step.title)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(theme.SIZE_CONTROL)

    def set_state(self, current: bool, done: bool) -> None:
        # `done` is a Qt dynamic property so the stylesheet can style a finished step
        # without this widget owning any colours of its own.
        self.setChecked(current)
        self.setProperty("done", done and not current)
        self.style().unpolish(self)
        self.style().polish(self)


class StepFlow(QWidget):
    """Header, one page at a time, and a bar whose primary button is the next move."""

    step_changed = Signal(int)
    finished = Signal()

    def __init__(self, steps: list[Step], finish_label: str = "Done") -> None:
        super().__init__()
        self.steps = steps
        self._reached = 0
        self._finish_label = finish_label

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # -- header ------------------------------------------------------------------
        header = QWidget()
        header.setObjectName("stepHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(theme.SPACE_5, theme.SPACE_3,
                                         theme.SPACE_5, theme.SPACE_3)
        header_layout.setSpacing(theme.SPACE_2)
        self.dots: list[StepDot] = []
        for index, step in enumerate(steps):
            dot = StepDot(index, step)
            dot.clicked.connect(lambda _=False, i=index: self.go_to(i))
            header_layout.addWidget(dot)
            self.dots.append(dot)
            if index < len(steps) - 1:
                line = QFrame()
                line.setObjectName("stepLine")
                line.setFrameShape(QFrame.Shape.HLine)
                line.setFixedHeight(1)
                line.setMinimumWidth(theme.SPACE_5)
                header_layout.addWidget(line, 1)
        outer.addWidget(header)

        # -- pages -------------------------------------------------------------------
        self.stack = QStackedWidget()
        scroller = QScrollArea()
        scroller.setWidgetResizable(True)
        scroller.setFrameShape(QScrollArea.Shape.NoFrame)
        scroller.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroller.setWidget(self.stack)
        outer.addWidget(scroller, 1)

        # -- action bar --------------------------------------------------------------
        bar = QWidget()
        bar.setObjectName("stepBar")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(theme.SPACE_5, theme.SPACE_3,
                                      theme.SPACE_5, theme.SPACE_3)
        bar_layout.setSpacing(theme.SPACE_3)
        self.status = QLabel("")
        self.status.setObjectName("subtle")
        self.status.setWordWrap(True)
        bar_layout.addWidget(self.status, 1)
        self.back_button = QPushButton("Back")
        self.back_button.setObjectName("text")
        self.back_button.clicked.connect(lambda: self.go_to(self.current() - 1))
        bar_layout.addWidget(self.back_button)
        self.next_button = QPushButton("Next")
        self.next_button.setObjectName("primary")
        self.next_button.setMinimumWidth(theme.BOX_SM + theme.SPACE_5)
        self.next_button.clicked.connect(self._advance)
        bar_layout.addWidget(self.next_button)
        outer.addWidget(bar)

    # -- pages -------------------------------------------------------------------------
    def add_page(self, widget: QWidget) -> None:
        self.stack.addWidget(widget)

    def current(self) -> int:
        return self.stack.currentIndex()

    def go_to(self, index: int) -> None:
        if not 0 <= index < len(self.steps):
            return
        # A stacked widget is as tall as its tallest page, so a short settings form would
        # sit above a screen of empty space and a preview would stretch past the fold.
        # Pages that are not showing are told to ask for no room at all.
        for position in range(self.stack.count()):
            policy = (QSizePolicy.Policy.Preferred if position == index
                      else QSizePolicy.Policy.Ignored)
            self.stack.widget(position).setSizePolicy(policy, policy)
        self.stack.setCurrentIndex(index)
        self.stack.updateGeometry()
        self._reached = max(self._reached, index)
        self._sync()
        self.step_changed.emit(index)

    def _advance(self) -> None:
        if self.current() >= len(self.steps) - 1:
            self.finished.emit()
        else:
            self.go_to(self.current() + 1)

    def set_status(self, text: str) -> None:
        self.status.setText(text)

    def set_next_enabled(self, enabled: bool) -> None:
        self.next_button.setEnabled(enabled)

    def _sync(self) -> None:
        index = self.current()
        for dot in self.dots:
            dot.set_state(dot.index == index, dot.index <= self._reached)
        self.back_button.setVisible(index > 0)
        last = index == len(self.steps) - 1
        self.next_button.setText(self._finish_label if last else "Next")
        hint = self.steps[index].hint if 0 <= index < len(self.steps) else ""
        if hint and not self.status.text():
            self.status.setText(hint)
