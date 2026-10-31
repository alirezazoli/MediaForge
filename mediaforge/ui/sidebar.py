"""Left navigation: a Material 3 drawer -- icons, grouped sections, reorderable.

Rebuilt 2026-08-17 after "the sidebar is vague and confusing". Three things were wrong,
and each was a navigation problem rather than a decoration one:

  * **Seventeen labels in one undifferentiated list.** Nothing said that "Photos to
    video" and "Settings" are different kinds of thing. They are grouped now, in the
    order the work happens: create, polish, assemble, manage.

  * **No icons.** A list of words has to be read; a list of icons is recognised. Windows
    ships Segoe Fluent Icons, so the glyphs are native and cost nothing to draw.

  * **"Advanced" hid half the app behind a toggle** most people never press. The sections
    do that job now -- everything is visible, but organised.

The order is still the user's: drag an entry to move it. Anything not in the saved order
falls back to its built-in position, so adding a panel in a future version cannot corrupt
an existing layout.
"""

from __future__ import annotations

from PySide6.QtCore import QMimeData, Qt, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout,
    QWidget,
)

from . import icons, theme

# (key, label, section, icon glyph from Segoe Fluent Icons)
NAV_ITEMS = [
    ("home",      "Home",             "",         ""),
    ("photos",    "Photos to video",  "CREATE",   "photos"),
    ("generate",  "Generate",         "CREATE",   "generate"),
    ("director",  "Director",         "CREATE",   "director"),
    ("templates", "Templates",        "CREATE",   "templates"),
    ("edit",      "Edit",             "POLISH",   "edit"),
    ("repair",    "Repair & upscale", "POLISH",   "repair"),
    ("batch",     "Batch repair",     "POLISH",   "batch"),
    ("enhance",   "Enhance",          "POLISH",   "enhance"),
    ("timeline",  "Timeline",         "ASSEMBLE", "timeline"),
    ("music",     "Find music",       "ASSEMBLE", "music"),
    ("library",   "Library",          "ASSEMBLE", "library"),
    ("agent",     "Assistant",        "MANAGE",   "assistant"),
    ("hub",       "Model Hub",        "MANAGE",   "hub"),
    ("learning",  "Learning",         "MANAGE",   "learning"),
    ("jobs",      "Jobs",             "MANAGE",   "jobs"),
    ("settings",  "Settings",         "MANAGE",   "settings"),
]

SECTION_ORDER = ["", "CREATE", "POLISH", "ASSEMBLE", "MANAGE"]

SECTION_OF = {key: section for key, _, section, _ in NAV_ITEMS}
LABEL_OF = {key: label for key, label, _, _ in NAV_ITEMS}
ICON_OF = {key: icon for key, _, _, icon in NAV_ITEMS}
DEFAULT_ORDER = [key for key, _, _, _ in NAV_ITEMS]

MIME_KEY = "application/x-mediaforge-nav"


EXPANDED_WIDTH = 236
COLLAPSED_WIDTH = 68


class NavButton(QPushButton):
    """A drawer entry: icon, label, drag-to-reorder."""

    reorder_requested = Signal(str, str)      # dragged key, dropped-on key

    def __init__(self, key: str, label: str, icon: str) -> None:
        super().__init__()
        self.key = key
        self.icon_glyph = icon
        # A single "&" in a button's text is a mnemonic marker, so "Repair & upscale"
        # renders as "Repair  upscale". Doubling it prints what the label says.
        self.plain_label = label.replace("&", "&&")
        self.setObjectName("navItem")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAcceptDrops(True)
        self.setToolTip(label)
        self._press_at = None
        self._collapsed = False
        self._apply_text()

    def _apply_text(self) -> None:
        # A QPushButton has ONE font, so an icon glyph in the text renders in the label's
        # font and comes out as a box. The glyph is painted into a real QIcon instead,
        # which leaves the label in Roboto where it belongs.
        icons.button(self, self.icon_glyph, size=17)
        self.setText("" if self._collapsed else f"   {self.plain_label}")

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = collapsed
        self._apply_text()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_at = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._press_at is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            return super().mouseMoveEvent(event)
        if (event.position().toPoint() - self._press_at).manhattanLength() < 12:
            return super().mouseMoveEvent(event)

        data = QMimeData()
        data.setData(MIME_KEY, self.key.encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(data)
        drag.exec(Qt.DropAction.MoveAction)
        self._press_at = None

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(MIME_KEY):
            event.acceptProposedAction()
            self.setStyleSheet(
                f"QPushButton#navItem {{ border-top: 2px solid {theme.ACCENT}; }}")

    def dragLeaveEvent(self, event) -> None:
        self.setStyleSheet("")

    def dropEvent(self, event) -> None:
        self.setStyleSheet("")
        if not event.mimeData().hasFormat(MIME_KEY):
            return
        source = bytes(event.mimeData().data(MIME_KEY)).decode("utf-8")
        if source and source != self.key:
            self.reorder_requested.emit(source, self.key)
        event.acceptProposedAction()

    def restyle(self) -> None:
        # The drop hint is an inline colour; drop it so a theme switch cannot leave the
        # previous palette's accent stuck on a button.
        self.setStyleSheet("")
        self._apply_text()


class Sidebar(QWidget):
    selected = Signal(str)
    order_changed = Signal(list)
    theme_changed = Signal(str)
    collapsed_changed = Signal(bool)

    def __init__(self, order: list[str] | None = None, mode: str = "dark") -> None:
        super().__init__()
        self.setObjectName("sidebar")
        # A QWidget subclass ignores a stylesheet background unless told to paint it;
        # without this the drawer was never filled and showed black on screen.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.expanded_width = EXPANDED_WIDTH
        self.collapsed = False
        self.setFixedWidth(self.expanded_width)

        self.order = self._sanitise(order)
        self.buttons: dict[str, NavButton] = {}
        self.section_labels: list[QLabel] = []
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._advanced = True          # every section is visible now

        outer = QVBoxLayout(self)
        outer.setContentsMargins(theme.SPACE_2, theme.SPACE_3, theme.SPACE_2, theme.SPACE_3)
        outer.setSpacing(theme.SPACE_2)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        self.brand = QLabel("MediaForge")
        self.brand.setObjectName("brand")
        header.addWidget(self.brand, 1)
        self.collapse_button = QPushButton()   # the glyph is the icon, not the text
        self.collapse_button.setObjectName("iconButton")
        icons.button(self.collapse_button, "menu", size=16)
        self.collapse_button.setFixedSize(theme.SIZE_CONTROL, theme.SIZE_CONTROL)
        self.collapse_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.collapse_button.setToolTip("Hide the sidebar (Ctrl+\\)")
        self.collapse_button.clicked.connect(lambda: self.set_collapsed(not self.collapsed))
        header.addWidget(self.collapse_button, 0)
        outer.addLayout(header)

        # The list scrolls: seventeen destinations do not fit a short window, and a nav
        # that clips its own last entry is worse than one that scrolls.
        self._nav_host = QWidget()
        self._nav_area = QVBoxLayout(self._nav_host)
        self._nav_area.setContentsMargins(0, 0, 0, 0)
        self._nav_area.setSpacing(2)
        scroller = QScrollArea()
        scroller.setWidgetResizable(True)
        scroller.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroller.setFrameShape(QScrollArea.Shape.NoFrame)
        scroller.setWidget(self._nav_host)
        outer.addWidget(scroller, 1)

        self.theme_row_widget = QWidget()
        theme_row = QHBoxLayout(self.theme_row_widget)
        theme_row.setContentsMargins(theme.SPACE_2, 0, 0, 0)
        theme_row.setSpacing(theme.SPACE_2)
        theme_caption = QLabel("Theme")
        theme_caption.setObjectName("faint")
        theme_row.addWidget(theme_caption)
        self.theme_box = QComboBox()
        self.theme_box.addItems(["dark", "light"])
        self.theme_box.setCurrentText(mode if mode in theme.MODES else "dark")
        self.theme_box.currentTextChanged.connect(self.theme_changed.emit)
        theme_row.addWidget(self.theme_box, 1)
        outer.addWidget(self.theme_row_widget)

        self.hint = QLabel("Drag an entry to reorder")
        self.hint.setObjectName("faint")
        outer.addWidget(self.hint)

        # Kept for the command and the tests that still reference it. Sections replaced
        # what it used to do, so it no longer hides anything.
        self.mode_button = QPushButton("Show advanced")
        self.mode_button.setObjectName("text")
        self.mode_button.setCheckable(True)
        self.mode_button.setChecked(True)
        self.mode_button.setVisible(False)
        self.mode_button.toggled.connect(self.set_advanced_visible)

        self._rebuild()

    # -- order ---------------------------------------------------------------------------
    @staticmethod
    def _sanitise(order: list[str] | None) -> list[str]:
        """Keeps a saved order usable across versions: drop unknown keys, append new."""
        clean = [key for key in (order or []) if key in LABEL_OF]
        seen = set(clean)
        clean += [key for key in DEFAULT_ORDER if key not in seen]
        return clean

    def _rebuild(self) -> None:
        while self._nav_area.count():
            item = self._nav_area.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
        self.buttons.clear()
        self.section_labels.clear()

        # Group the user's order by section, keeping their order inside each group.
        by_section: dict[str, list[str]] = {}
        for key in self.order:
            by_section.setdefault(SECTION_OF.get(key, ""), []).append(key)

        sections = SECTION_ORDER + [s for s in by_section if s not in SECTION_ORDER]
        for section in sections:
            keys = by_section.get(section)
            if not keys:
                continue
            if section:
                heading = QLabel(section)
                heading.setObjectName("navSection")
                self._nav_area.addWidget(heading)
                self.section_labels.append(heading)
            for key in keys:
                button = NavButton(key, LABEL_OF[key], ICON_OF[key])
                button.clicked.connect(lambda _=False, k=key: self.selected.emit(k))
                button.reorder_requested.connect(self._move_item)
                self._group.addButton(button)
                self.buttons[key] = button
                self._nav_area.addWidget(button)
        self._nav_area.addStretch(1)
        self._apply_visibility()

    def _move_item(self, source: str, target: str) -> None:
        if source not in self.order or target not in self.order:
            return
        self.order.remove(source)
        self.order.insert(self.order.index(target), source)
        self._rebuild()
        self.order_changed.emit(list(self.order))

    def reset_order(self) -> None:
        self.order = list(DEFAULT_ORDER)
        self._rebuild()
        self.order_changed.emit(list(self.order))

    # -- visibility -----------------------------------------------------------------------
    def _apply_visibility(self) -> None:
        for button in self.buttons.values():
            button.setVisible(True)
            button.set_collapsed(self.collapsed)
        for heading in self.section_labels:
            heading.setVisible(not self.collapsed)

    def set_advanced_visible(self, shown: bool) -> None:
        """Retained for the existing command and tests. Sections replaced the toggle, so
        nothing is hidden any more: every destination is always reachable."""
        self._advanced = True
        if self.mode_button.isChecked() != shown:
            self.mode_button.setChecked(shown)
        self._apply_visibility()

    def set_collapsed(self, collapsed: bool) -> None:
        """Shrinks to an icon rail, keeping every destination clickable.

        Collapsing to icons rather than to nothing is the point: the panels get the width
        back and navigation stays one click away, with tooltips carrying the names.
        """
        self.collapsed = collapsed
        self.setFixedWidth(COLLAPSED_WIDTH if collapsed else self.expanded_width)
        self.brand.setVisible(not collapsed)
        self.theme_row_widget.setVisible(not collapsed)
        self.hint.setVisible(not collapsed)
        self._apply_visibility()
        self.collapse_button.setToolTip(
            "Show the sidebar (Ctrl+\\)" if collapsed else "Hide the sidebar (Ctrl+\\)"
        )
        self.collapsed_changed.emit(collapsed)

    def select(self, key: str) -> None:
        button = self.buttons.get(key)
        if button is not None:
            button.setChecked(True)

    def badge_jobs(self, active: int) -> None:
        button = self.buttons.get("jobs")
        if button is None:
            return
        button.plain_label = f"Jobs  ({active})" if active else "Jobs"
        button._apply_text()

    def restyle(self) -> None:
        """Re-apply the per-state fonts after a theme switch, so the icon rail does not
        come back rendered in the body font."""
        self._apply_visibility()
