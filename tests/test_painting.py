"""Backgrounds must actually be painted.

`QWidget.grab()` starts from a filled pixmap, so an unpainted background looks fine in a
screenshot taken that way -- and shows as solid black on the real screen, where an
unpainted window region is transparent, which Windows displays as black. That is how the
light theme shipped with a black page and drawer. These tests render into a fully
transparent image instead, which is what the screen effectively does.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage

from mediaforge.ui import theme


def _render(widget) -> QImage:
    image = QImage(widget.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    widget.render(image)
    return image


def _at(image: QImage, window, child, x: int, y: int) -> QColor:
    point = child.mapTo(window, QPoint(x, y))
    return image.pixelColor(point.x(), point.y())


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_page_and_drawer_are_painted_in_their_own_colours(window, qapp, mode):
    window.apply_theme(mode)
    window.go_to("home")
    qapp.processEvents()
    image = _render(window)
    palette = theme.PALETTES[mode]

    # Top-right of the page and top-left of the drawer: margins, never content.
    page = _at(image, window, window.stack, window.stack.width() - 4, 4)
    drawer = _at(image, window, window.sidebar, 3, 3)
    for name, colour, expected in (("page", page, palette["surface"]),
                                   ("drawer", drawer, palette["surfaceContainerLow"])):
        assert colour.alpha() == 255, f"{mode} {name} is not painted (black on screen)"
        assert colour.name() == QColor(expected).name(), (mode, name, colour.name(), expected)


def test_no_screen_leaves_the_window_unpainted(window, qapp):
    window.apply_theme("light")
    for key in window.panels:
        window.go_to(key)
        qapp.processEvents()
        image = _render(window)
        page = _at(image, window, window.stack, window.stack.width() - 4, window.stack.height() // 2)
        assert page.alpha() == 255, f"{key}: unpainted page region"


def test_no_inline_style_uses_an_eight_digit_colour(window, qapp):
    """'#RRGGBB' with two digits appended is CSS alpha to a person and #AARRGGBB to Qt.
    It has turned two banners into solid maroon bands so far; this catches a third."""
    import re

    from PySide6.QtWidgets import QWidget

    pattern = re.compile(r"#[0-9a-fA-F]{8}\b")
    for mode in ("light", "dark"):
        window.apply_theme(mode)
        for key in window.panels:
            window.go_to(key)
            qapp.processEvents()
        offenders = [(widget.objectName() or type(widget).__name__, widget.styleSheet())
                     for widget in window.findChildren(QWidget)
                     if pattern.search(widget.styleSheet() or "")]
        assert not offenders, offenders[:3]
