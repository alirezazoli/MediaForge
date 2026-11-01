"""Text has to be readable: every text colour against every surface it sits on.

WCAG asks 4.5:1 for normal-size text. The faint text used to borrow the border colour
(4.2:1) and warnings used tone-40 amber (4.0:1); both read as washed out.
"""

from __future__ import annotations

import pytest

from mediaforge.ui import theme

TEXT_ROLES = ["onSurface", "onSurfaceVariant", "onSurfaceFaint", "primary", "success",
              "warning", "error"]
SURFACES = ["surface", "surfaceContainerLowest", "surfaceContainerLow", "surfaceContainer",
            "surfaceContainerHigh"]
PAIRS = [("onPrimary", "primary"), ("onSecondaryContainer", "secondaryContainer"),
         ("onSuccessContainer", "successContainer"),
         ("onWarningContainer", "warningContainer"),
         ("onErrorContainer", "errorContainer"), ("inverseOnSurface", "inverseSurface")]


def _luminance(colour: str) -> float:
    def channel(value: int) -> float:
        value /= 255
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    red, green, blue = (int(colour.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)


def contrast(front: str, back: str) -> float:
    lighter, darker = sorted((_luminance(front), _luminance(back)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


@pytest.mark.parametrize("mode", theme.MODES)
def test_text_roles_are_readable_on_every_surface(mode):
    palette = theme.PALETTES[mode]
    weak = [(text, surface, round(contrast(palette[text], palette[surface]), 2))
            for text in TEXT_ROLES for surface in SURFACES
            if contrast(palette[text], palette[surface]) < 4.5]
    weak += [(text, back, round(contrast(palette[text], palette[back]), 2))
             for text, back in PAIRS if contrast(palette[text], palette[back]) < 4.5]
    assert not weak, weak


@pytest.mark.parametrize("mode", theme.MODES)
def test_status_badges_are_readable(mode):
    theme.set_mode(mode)
    palette = theme.PALETTES[mode]
    for role in ("primary", "success", "warning", "error"):
        background = theme.tint(palette[role], 0.16)
        assert contrast(palette[role], background) >= 4.5, (mode, role)
