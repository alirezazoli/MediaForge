"""Start screen: pick a job, not a tool.

The old design opened on a ten-tab wall of forms. This opens on four things a person
might actually want to do, plus an honest readout of what is set up.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy,
    QVBoxLayout, QWidget,
)

from .. import availability
from ..context import AppContext
from . import theme


class TaskCard(QFrame):
    """A big clickable card. Whole surface is the button."""

    clicked = Signal()

    def __init__(self, icon: str, title: str, subtitle: str, tag: str = "") -> None:
        super().__init__()
        self.setObjectName("card")
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setMinimumHeight(150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(6)

        top = QHBoxLayout()
        glyph = QLabel(icon)
        glyph.setObjectName("glyph")
        top.addWidget(glyph)
        top.addStretch(1)
        if tag:
            badge = QLabel(tag)
            badge.setObjectName("freeBadge")
            top.addWidget(badge)
        layout.addLayout(top)

        heading = QLabel(title)
        heading.setObjectName("h2")
        layout.addWidget(heading)

        body = QLabel(subtitle)
        body.setObjectName("subtle")
        body.setWordWrap(True)
        layout.addWidget(body)
        layout.addStretch(1)

    def enterEvent(self, event) -> None:
        # Rebuilt from the CURRENT accent every time rather than captured once, so a
        # theme switch cannot leave the previous palette's highlight behind.
        self.setStyleSheet(f"QFrame#card {{ border: 1px solid {theme.ACCENT}; }}")
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.setStyleSheet("")
        super().leaveEvent(event)

    def restyle(self) -> None:
        self.setStyleSheet("")

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class HomePanel(QWidget):
    status = Signal(str)
    navigate = Signal(str)      # panel key

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        self._build()

    def _build(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(18)

        title = QLabel("What are we making?")
        title.setObjectName("h1")
        layout.addWidget(title)

        self.project_label = QLabel("")
        self.project_label.setObjectName("subtle")
        layout.addWidget(self.project_label)

        grid = QGridLayout()
        grid.setSpacing(14)
        cards = [
            ("🖼", "Photos to video", "Drop photos, get a clip with slow camera moves, "
                                     "crossfades and music. Nothing else needed.",
             "", "photos"),
            ("✂", "Edit a video", "Trim, cut silence, reframe for social, stabilise, "
                                  "titles, colour, subtitles, export presets.",
             "", "edit"),
            ("✨", "Enhance", "Upscale, sharpen, remove background, transcribe, "
                             "separate audio — all on your own machine.",
             "", "enhance"),
            ("🎬", "Generate with AI", "Text or a photo into new video and images. "
                                       "Needs an API key, or a local ComfyUI.",
             "", "generate"),
            ("🎥", "Direct a whole video", "Describe it once. It writes the shots, makes "
                                           "them, and assembles the cut.",
             "", "director"),
            ("📐", "Start from a template", "Reel hooks, product films, slideshows, "
                                            "explainers, trailers.",
             "", "templates"),
        ]
        for index, (icon, heading, body, tag, key) in enumerate(cards):
            card = TaskCard(icon, heading, body, tag)
            card.clicked.connect(lambda k=key: self.navigate.emit(k))
            grid.addWidget(card, index // 3, index % 3)
        layout.addLayout(grid)

        status_title = QLabel("Setup")
        status_title.setObjectName("h2")
        layout.addWidget(status_title)

        self.status_box = QLabel("")
        self.status_box.setObjectName("subtle")
        self.status_box.setWordWrap(True)
        self.status_box.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.status_box)

        button_row = QHBoxLayout()
        for label, key in (("Open Settings", "settings"), ("Model Hub", "hub"),
                           ("Library", "library")):
            button = QPushButton(label)
            button.setObjectName("ghost")
            button.clicked.connect(lambda _=False, k=key: self.navigate.emit(k))
            button_row.addWidget(button)
        button_row.addStretch(1)
        layout.addLayout(button_row)
        layout.addStretch(1)

        scroll.setWidget(host)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def refresh(self) -> None:
        project = self.context.project
        self.project_label.setText(
            f"Project: {project.name}  ·  {project.root}" if project else "No project open"
        )

        lines: list[str] = []
        if self.context.locator.available:
            lines.append(theme.badge("ready", theme.SUCCESS)
                         + " ffmpeg — editing, photo clips and rendering all work")
        else:
            lines.append(theme.badge("missing", theme.DANGER)
                         + " ffmpeg — install it in Settings, nothing local works without it")

        installed = [tool.name for tool in self.context.local_tools.installed_tools()]
        if installed:
            lines.append(theme.badge(f"{len(installed)} installed", theme.SUCCESS)
                         + " local engines — " + ", ".join(installed[:5]))
        else:
            lines.append(theme.badge("none yet", theme.TEXT_MUTED)
                         + " local AI engines — install upscaling, transcription and more "
                           "from the Model Hub")

        configured = [provider.name for provider in self.context.registry.all
                      if provider.credential_slot and provider.configured]
        if configured:
            lines.append(theme.badge(f"{len(configured)} keys", theme.SUCCESS)
                         + " API providers — " + ", ".join(configured))
        else:
            lines.append(theme.badge("no keys", theme.TEXT_MUTED)
                         + " API providers — AI generation is locked until you add one in "
                           "Settings. Everything else still works.")

        free = availability.free_models(self.context.registry)
        if free:
            lines.append(theme.badge(f"{len(free)} need no key", theme.FREE)
                         + " models need no key at all")

        self.status_box.setText("<br><br>".join(lines))
