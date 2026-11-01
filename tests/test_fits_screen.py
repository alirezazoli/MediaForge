"""Every screen has to fit a laptop, not just the monitor it was designed on.

A 1920x1080 or 1920x1200 laptop at Windows' recommended 150% scaling gives Qt 1280x720 to
1280x800 logical pixels. The window used to insist on 1500x760, so it opened wider than the
screen; and when a page needs more width than it gets, Qt does not wrap or scroll -- it
squeezes widgets below their minimum and draws them on top of each other. These tests
size the window like that laptop and look for both failures on every page and every step.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QAbstractScrollArea, QScrollArea, QSizePolicy, QWidget

# (width, height, sidebar collapsed): a 150% laptop, and the smallest window still supported.
SIZES = [(1280, 720, False), (1024, 640, True)]


def _problems(page: QWidget) -> list[str]:
    found = []
    for widget in [page] + page.findChildren(QWidget):
        if not widget.isVisible() or isinstance(widget.parentWidget(), QAbstractScrollArea):
            continue  # scroll bars and viewports are sized by their area
        # What a layout actually honours: an explicit minimum wins over the hint, and an
        # Ignored policy asks for nothing (Qt's qSmartMinSize).
        if widget.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Ignored:
            continue
        needed = widget.minimumWidth() or widget.minimumSizeHint().width()
        name = widget.objectName() or type(widget).__name__
        text = getattr(widget, "text", None)
        label = f"{name} {text()[:40]!r}" if callable(text) else name
        if needed > 0 and widget.width() < needed - 1:
            found.append(f"squeezed {label}: {widget.width()} < {needed}")
    for area in page.findChildren(QScrollArea):
        content = area.widget()
        if area.isVisible() and content is not None and \
                content.width() > area.viewport().width() + 1:
            found.append(f"too wide for its scroll area: {type(content).__name__} "
                         f"{content.width()} > {area.viewport().width()}")
    return found


def _steps(panel) -> range:
    flow = getattr(panel, "flow", None)
    return range(len(flow.steps)) if flow is not None else range(1)


@pytest.mark.parametrize("width,height,collapsed", SIZES)
def test_every_page_fits_a_laptop_screen(window, qapp, width, height, collapsed):
    window.sidebar.set_collapsed(collapsed)
    window.resize(width, height)
    qapp.processEvents()
    assert window.width() <= width and window.height() <= height, \
        f"the window refuses to be smaller than {window.minimumSize().toTuple()}"

    report = []
    for key, panel in window.panels.items():
        window.go_to(key)
        for step in _steps(panel):
            if step:
                panel.flow.go_to(step)
            qapp.processEvents()
            report += [f"{key}[{step}] {problem}" for problem in _problems(panel)]
    assert not report, "\n".join(report[:40])


def test_the_window_opens_inside_the_screen(window, qapp):
    window.showNormal()
    window.show_fitted()
    qapp.processEvents()
    available = window.screen().availableGeometry()
    frame = window.frameGeometry()
    assert available.contains(frame) or window.isMaximized(), (frame, available)
