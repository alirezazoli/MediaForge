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
    # editing tools (codepoints checked against SegoeIcons.ttf by rendering a sheet)
    "trim": "", "silence": "", "scenes": "", "speed": "",
    "reverse": "", "loop": "", "fade": "", "phone": "",
    "rotate": "", "convert": "", "palette": "", "layers": "",
    "sharpen": "", "deband": "", "lines": "", "hdr": "",
    "camera": "", "stamp": "", "text": "", "pip": "",
    "split": "", "key": "", "blur": "", "captions": "",
    "volume": "", "mic": "", "headphones": "", "photo": "",
    "frames": "", "grid": "", "wave": "", "gif": "",
    "export": "", "video": "", "sliders": "", "chat": "",
    "list": "", "stopwatch": "", "monitor": "",
    "box": "", "box-checked": "",
}


def _resolve_colour(colour: str | None) -> str:
    """A colour, or `@TOKEN` naming a theme token read at paint time.

    A token rather than its value, so an icon tinted with the accent picks up the new
    accent when the theme switches instead of keeping the old mode's hex.
    """
    if colour and colour.startswith("@"):
        return getattr(theme, colour[1:], "") or theme.TEXT_MUTED or "#888888"
    return colour or theme.TEXT_MUTED or "#888888"


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
    painter.setPen(QColor(_resolve_colour(colour)))
    painter.drawText(canvas.rect(), Qt.AlignmentFlag.AlignCenter, glyph)
    painter.end()
    # Without the ratio a QLabel shows the 2x canvas at twice the asked-for size; with
    # it, labels and icons both lay it out at `size` and use the extra pixels for detail.
    canvas.setDevicePixelRatio(scale)
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
