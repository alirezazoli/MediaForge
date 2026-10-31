"""Icons, from the font Windows already ships.

Segoe Fluent Icons is installed on Windows 11, so an icon costs nothing to ship and
matches the rest of the desktop. Two things this module exists to handle:

  * **A QPushButton has one font.** An icon character written into the label renders in
    the label's font and comes out as an empty box. The glyph is painted into a QIcon
    instead, which leaves the words in the UI face where they belong.

  * **An icon has to follow the theme.** Painting it once at construction leaves last
    theme's grey on this theme's white, so `apply()` is called again on a restyle.

Names, not codepoints, at the call site: `icons.button(b, "add")` rather than a magic
`\\uE710` that nobody can read six months later.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap

from . import theme

# Segoe Fluent Icons codepoints, by what they mean here rather than what they are called.
GLYPHS: dict[str, str] = {
    # navigation
    "home": "", "photos": "", "generate": "", "director": "",
    "templates": "", "edit": "", "repair": "", "batch": "",
    "enhance": "", "timeline": "", "music": "", "library": "",
    "assistant": "", "hub": "", "learning": "", "jobs": "",
    "settings": "", "menu": "",
    # actions
    "add": "", "folder": "", "remove": "", "clear": "",
    "play": "", "pause": "", "stop": "", "open": "",
    "save": "", "refresh": "", "search": "", "download": "",
    "check": "", "close": "", "back": "", "next": "",
    "up": "", "down": "", "more": "", "info": "",
    "warning": "", "wand": "", "measure": "", "cut": "",
    "crop": "", "sound": "", "mute": "", "eye": "",
    "compare": "", "sparkle": "", "film": "", "image": "",
}


def pixmap(name: str, colour: str | None = None, size: int = 16) -> QPixmap:
    """One glyph, painted at 2x so it stays crisp when scaled down."""
    glyph = GLYPHS.get(name, name)
    scale = 2
    canvas = QPixmap(size * scale, size * scale)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    font = QFont("Segoe Fluent Icons")
    font.setPixelSize(int(size * scale * 0.86))
    painter.setFont(font)
    painter.setPen(QColor(colour or theme.TEXT_MUTED or "#888888"))
    painter.drawText(canvas.rect(), Qt.AlignmentFlag.AlignCenter, glyph)
    painter.end()
    return canvas


def icon(name: str, colour: str | None = None, size: int = 16) -> QIcon:
    return QIcon(pixmap(name, colour, size))


def button(widget, name: str, colour: str | None = None, size: int = 16):
    """Gives a button an icon and remembers which one, so a theme switch can repaint it.

    The name is stored on the widget rather than in a registry: the widget outlives any
    bookkeeping this module could keep, and `restyle_tree` walks widgets.
    """
    widget.setProperty("iconName", name)
    widget.setProperty("iconColour", colour or "")
    widget.setIcon(icon(name, colour, size))
    widget.setIconSize(QSize(size, size))
    return widget


def repaint(widget) -> bool:
    """Re-applies a widget's remembered icon in the current palette."""
    name = widget.property("iconName")
    if not name:
        return False
    colour = widget.property("iconColour") or None
    size = widget.iconSize().width() or 16
    widget.setIcon(icon(name, colour, size))
    return True


def repaint_tree(root) -> int:
    """Repaints every remembered icon under a widget. Called after a theme switch."""
    from PySide6.QtWidgets import QWidget

    return sum(1 for widget in [root] + root.findChildren(QWidget) if repaint(widget))
