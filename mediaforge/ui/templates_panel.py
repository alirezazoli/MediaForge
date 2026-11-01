"""Templates panel: start from a shape instead of a blank timeline."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QSplitter, QTextEdit, QVBoxLayout, QWidget,
)

from ..catalog import load_catalog
from ..context import AppContext


class TemplatesPanel(QWidget):
    status = Signal(str)
    timeline_changed = Signal()
    brief_requested = Signal(str, dict)   # brief text, canvas

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        self._templates: list[dict] = []
        self._build()
        self.refresh()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Pick a shape, then fill the slots from your Library selection. Templates are "
            "editable JSON — add your own in the Model Hub -> Edit catalogue folder."
        ))

        splitter = QSplitter(Qt.Orientation.Horizontal)

        splitter.setChildrenCollapsible(False)

        splitter.setHandleWidth(8)
        self.list_widget = QListWidget()
        self.list_widget.currentRowChanged.connect(self._show_detail)
        self.list_widget.setMinimumWidth(260)
        splitter.addWidget(self.list_widget)

        right = QWidget()
        right.setMinimumWidth(360)
        right_layout = QVBoxLayout(right)
        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        right_layout.addWidget(self.detail, 1)

        button_row = QHBoxLayout()
        apply_button = QPushButton("Apply to timeline from Library selection")
        apply_button.setObjectName("primary")
        apply_button.clicked.connect(self.apply_template)
        brief_button = QPushButton("Send brief to Director")
        brief_button.clicked.connect(self.send_brief)
        reload_button = QPushButton("Reload templates")
        reload_button.clicked.connect(self.refresh)
        for button in (apply_button, brief_button, reload_button):
            button_row.addWidget(button)
        button_row.addStretch(1)
        right_layout.addLayout(button_row)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)

    def set_selection_provider(self, provider) -> None:
        self._selection_provider = provider

    def refresh(self) -> None:
        try:
            data = load_catalog("templates.json")
        except Exception as error:
            self.detail.setPlainText(f"Could not load templates.json: {error}")
            return
        self._templates = data.get("templates", [])
        self.list_widget.clear()
        for template in self._templates:
            item = QListWidgetItem(f"{template.get('name', '')}\n{template.get('category', '')}")
            self.list_widget.addItem(item)
        if self._templates:
            self.list_widget.setCurrentRow(0)

    def _current(self) -> dict | None:
        row = self.list_widget.currentRow()
        return self._templates[row] if 0 <= row < len(self._templates) else None

    def _show_detail(self) -> None:
        template = self._current()
        if not template:
            self.detail.clear()
            return
        canvas = template.get("canvas", {})
        slots = template.get("slots", [])
        total = sum(float(slot.get("seconds", 0)) for slot in slots)
        lines = [
            template.get("name", ""), "",
            template.get("description", ""), "",
            f"Canvas   : {canvas.get('width')}x{canvas.get('height')} @ {canvas.get('fps')} fps",
            f"Crossfade: {template.get('crossfade', 0)} s",
            f"Runtime  : about {total:.0f} s across {len(slots)} slots",
            "",
            "Slots:",
        ]
        for index, slot in enumerate(slots, start=1):
            text = f"  text: \"{slot['text']}\"" if slot.get("text") else ""
            lines.append(f"  {index}. {slot.get('label', '')}  ({slot.get('seconds')}s){text}")
        if template.get("slot_repeat"):
            lines.append("  (this slot repeats for every asset you select)")
        if template.get("recommended_steps"):
            lines += ["", "Recommended follow-up:"]
            lines += [f"  - {step}" for step in template["recommended_steps"]]
        if template.get("director_brief"):
            lines += ["", "Director brief:", template["director_brief"]]
        self.detail.setPlainText("\n".join(lines))

    # -- actions -----------------------------------------------------------------------
    def apply_template(self) -> None:
        template = self._current()
        if not template:
            return
        if self.context.project is None:
            QMessageBox.information(self, "No project", "Create or open a project first.")
            return

        assets = getattr(self, "_selection_provider", lambda: [])()
        usable = [asset for asset in assets if asset.kind in ("video", "image")]
        slots = template.get("slots", [])
        if template.get("slot_repeat") and usable:
            slots = [dict(slots[0]) for _ in usable]

        if not usable:
            QMessageBox.information(
                self, "Select assets first",
                f"'{template.get('name')}' has {len(slots)} slots.\n\n"
                "Select that many clips or photos in the Library, then apply the template. "
                "They fill the slots in selection order.",
            )
            return

        project = self.context.require_project()
        project.clear_timeline()
        filled = 0
        for index, slot in enumerate(slots):
            if index >= len(usable):
                break
            asset = usable[index]
            seconds = float(slot.get("seconds", 3.0))
            # A photo lasts as long as its slot. (Libraries made before 1.0.1 stored one
            # frame's 0.04 s as a photo's duration.)
            out_point = (seconds if asset.kind == "image" or not asset.duration
                         else min(seconds, asset.duration))
            project.append_clip(asset.id, 0.0, out_point)
            filled += 1

        canvas = template.get("canvas", {})
        project.set_meta("template", template.get("id", ""))
        project.set_meta("canvas_width", str(canvas.get("width", 1920)))
        project.set_meta("canvas_height", str(canvas.get("height", 1080)))
        project.set_meta("canvas_fps", str(canvas.get("fps", 30)))
        project.set_meta("crossfade", str(template.get("crossfade", 0.5)))

        self.timeline_changed.emit()
        message = f"Applied '{template.get('name')}': {filled} of {len(slots)} slots filled"
        if filled < len(slots):
            message += " — select more assets to fill the rest"
        self.status.emit(message)

    def send_brief(self) -> None:
        template = self._current()
        if not template:
            return
        brief = template.get("director_brief", "")
        if not brief:
            QMessageBox.information(
                self, "No brief",
                f"'{template.get('name')}' is a layout template with no Director brief. "
                "Apply it to the timeline and fill the slots from your Library instead.",
            )
            return
        self.brief_requested.emit(brief, template.get("canvas", {}))
        self.status.emit(f"Brief from '{template.get('name')}' sent to the Director tab")
