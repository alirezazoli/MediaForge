"""The design system: Material 3, in Google's own colours and type.

Rebuilt 2026-08-17. The previous palette was hand-picked hex with a single "surface"
tone, so every region sat at the same visual depth and the only way to separate one from
another was to draw a border around it. That is what made the window read as a pile of
boxes.

Material 3 layers with **tone**, not outlines: five surface-container levels, each a step
lighter (or darker) than the one beneath, so a card reads as sitting on the page without
a line around it. The colours here are Google's own -- the blue tonal ramp from Workspace
and Gemini, and Google's neutral grey -- and the type scale is M3's, set in Roboto, which
is installed on this machine.

The old token names (BG, SURFACE_2, ACCENT, ...) are kept as aliases so the seventeen
panels that use them keep working; each now resolves to the matching M3 role.
"""

from __future__ import annotations

# ======================================================================================
# Tonal palettes -- Google blue, Google neutral
# ======================================================================================
# Tone numbers are the Material 3 convention: 0 is black, 100 is white, and a role picks
# a tone rather than a hand-mixed colour. Keeping the whole ramp means a new role can be
# added later without anyone inventing a colour for it.

BLUE = {
    0: "#000000", 10: "#041e49", 20: "#062e6f", 30: "#0842a0", 40: "#0b57d0",
    50: "#1b6ef3", 60: "#4c8df6", 70: "#7cacf8", 80: "#a8c7fa", 90: "#d3e3fd",
    95: "#ecf3fe", 99: "#fafbff", 100: "#ffffff",
}
NEUTRAL = {
    0: "#000000", 4: "#0e0e0e", 6: "#131314", 10: "#1f1f1f", 12: "#1e1f20",
    17: "#282a2c", 20: "#303030", 22: "#333537", 24: "#37393b", 30: "#474747",
    40: "#5e5e5e", 50: "#757575", 60: "#8f8f8f", 70: "#ababab", 80: "#c7c7c7",
    87: "#dde3ea", 90: "#e3e3e3", 92: "#e9eef6", 94: "#f0f4f9", 95: "#f2f2f2",
    96: "#f8fafd", 98: "#fbfcff", 99: "#fdfcfb", 100: "#ffffff",
}
NEUTRAL_VARIANT = {
    30: "#444746", 50: "#747775", 60: "#8e918f", 80: "#c4c7c5", 90: "#e1e3e1",
}
GREEN = {10: "#072711", 30: "#0f5223", 40: "#146c2e", 80: "#6dd58c", 90: "#c4eed0"}
YELLOW = {10: "#3f2e00", 30: "#7a5900", 40: "#a56800", 80: "#f9c168", 90: "#ffdfa0"}
RED = {10: "#410e0b", 30: "#8c1d18", 40: "#b3261e", 80: "#f2b8b5", 90: "#f9dedc"}


def _scheme(light: bool) -> dict[str, str]:
    """One Material 3 colour scheme.

    Every value is a tone from the ramps above rather than a fresh colour, which is what
    keeps light and dark consistent with each other instead of being two designs.
    """
    if light:
        return {
            "primary": BLUE[40], "onPrimary": BLUE[100],
            "primaryContainer": BLUE[90], "onPrimaryContainer": BLUE[10],
            "primaryHover": BLUE[30],
            "secondaryContainer": BLUE[90], "onSecondaryContainer": BLUE[10],
            "tertiaryContainer": NEUTRAL[92], "onTertiaryContainer": NEUTRAL[10],
            # The page is a soft blue-grey and the cards are white, the way Workspace
            # does it. The previous light scheme put the page at 98 and cards at 96 --
            # a difference of about one percent, so nothing read as layered and the
            # whole screen looked flat and arbitrary.
            "surface": NEUTRAL[96],              # page
            "surfaceContainerLowest": NEUTRAL[100],
            "surfaceContainerLow": NEUTRAL[100],  # cards: white, and they pop
            "surfaceContainer": NEUTRAL[94],      # drawer
            "surfaceContainerHigh": NEUTRAL[92],  # buttons, hover
            "surfaceContainerHighest": NEUTRAL[87],
            "onSurface": NEUTRAL[10],
            "onSurfaceVariant": NEUTRAL_VARIANT[30],
            "outline": NEUTRAL_VARIANT[50],
            "outlineVariant": NEUTRAL_VARIANT[80],
            "inverseSurface": NEUTRAL[20], "inverseOnSurface": NEUTRAL[95],
            "success": GREEN[40], "successContainer": GREEN[90],
            "onSuccessContainer": GREEN[10],
            "warning": YELLOW[40], "warningContainer": YELLOW[90],
            "onWarningContainer": YELLOW[10],
            "error": RED[40], "errorContainer": RED[90], "onErrorContainer": RED[10],
            "scrim": NEUTRAL[80], "scrimHover": NEUTRAL[70],
        }
    return {
        "primary": BLUE[80], "onPrimary": BLUE[20],
        "primaryContainer": BLUE[30], "onPrimaryContainer": BLUE[90],
        "primaryHover": BLUE[90],
        "secondaryContainer": BLUE[30], "onSecondaryContainer": BLUE[90],
        "tertiaryContainer": NEUTRAL[24], "onTertiaryContainer": NEUTRAL[90],
        "surface": NEUTRAL[6],
        "surfaceContainerLowest": NEUTRAL[4],
        "surfaceContainerLow": NEUTRAL[10],
        "surfaceContainer": NEUTRAL[12],
        "surfaceContainerHigh": NEUTRAL[17],
        "surfaceContainerHighest": NEUTRAL[22],
        "onSurface": NEUTRAL[90],
        "onSurfaceVariant": NEUTRAL_VARIANT[80],
        "outline": NEUTRAL_VARIANT[60],
        "outlineVariant": NEUTRAL_VARIANT[30],
        "inverseSurface": NEUTRAL[90], "inverseOnSurface": NEUTRAL[20],
        "success": GREEN[80], "successContainer": GREEN[30],
        "onSuccessContainer": GREEN[90],
        "warning": YELLOW[80], "warningContainer": YELLOW[30],
        "onWarningContainer": YELLOW[90],
        "error": RED[80], "errorContainer": RED[30], "onErrorContainer": RED[90],
        "scrim": NEUTRAL[30], "scrimHover": NEUTRAL[40],
    }


def _with_aliases(scheme: dict[str, str]) -> dict[str, str]:
    """Adds the old token names so existing panels keep working unchanged."""
    scheme = dict(scheme)
    scheme.update({
        "BG": scheme["surface"],
        "SURFACE": scheme["surfaceContainerLow"],
        "SURFACE_2": scheme["surfaceContainer"],
        "SURFACE_3": scheme["surfaceContainerHigh"],
        "BORDER": scheme["outlineVariant"],
        "BORDER_SOFT": scheme["outlineVariant"],
        "TEXT": scheme["onSurface"],
        "TEXT_MUTED": scheme["onSurfaceVariant"],
        "TEXT_FAINT": scheme["outline"],
        "ACCENT": scheme["primary"],
        "ACCENT_HOVER": scheme["primaryHover"],
        "ACCENT_DIM": scheme["primaryContainer"],
        "ON_ACCENT": scheme["onPrimary"],
        "SUCCESS": scheme["success"],
        "FREE": scheme["success"],
        "WARNING": scheme["warning"],
        "DANGER": scheme["error"],
        "SCROLL": scheme["scrim"],
        "SCROLL_HOVER": scheme["scrimHover"],
    })
    return scheme


PALETTES: dict[str, dict[str, str]] = {
    "light": _with_aliases(_scheme(True)),
    "dark": _with_aliases(_scheme(False)),
}
MODES = list(PALETTES)

# ======================================================================================
# Shape, spacing, type
# ======================================================================================
SHAPE_XS, SHAPE_SM, SHAPE_MD, SHAPE_LG, SHAPE_XL = 4, 8, 12, 16, 28
SHAPE_FULL = 20                     # half the 40px button height
RADIUS = SHAPE_MD                   # old alias
RADIUS_SM = SHAPE_SM                # old alias

# ------------------------------------------------------------------ size scale ----
# Before this there were twenty-two different hardcoded pixel values across the panels
# -- 38, 60, 90, 110, 150, 190, 260, 340, 420, 560 -- none of them on any grid. That is
# what "everything is either too big or too small" is: no scale at all. Every fixed
# dimension in the app now comes from here.
# 40px buttons and 44px menu rows are phone sizes. On a desktop tool with this many
# controls they eat the screen and make every screen feel half empty. These are the
# desktop equivalents: still comfortably clickable, a third less tall.
SIZE_CONTROL = 32          # a button, an input: one row of interaction
SIZE_ROW = 36              # a list row, a nav entry
PANE_XS, PANE_SM, PANE_MD, PANE_LG = 240, 320, 400, 560     # column widths
BOX_SM, BOX_MD, BOX_LG, BOX_XL = 96, 160, 240, 320          # preview and drop areas

# The 8dp grid. Every margin and gap should be one of these.
SPACE_1, SPACE_2, SPACE_3, SPACE_4, SPACE_5, SPACE_6 = 4, 8, 12, 16, 24, 32

# Roboto reads soft and a little precious at UI sizes -- its round terminals and wide
# apertures are made for Android, not for a dense desktop tool. Segoe UI Variable is the
# native Windows 11 face: tighter, flatter, and it hints properly at 12-13px, which is
# where most of this app's text actually lives.
FONT_STACK = '"Segoe UI Variable Text", "Segoe UI", "Roboto", system-ui, sans-serif'
FONT_DISPLAY = '"Segoe UI Variable Display", "Segoe UI Variable Text", "Segoe UI"'
ICON_FONT = '"Segoe Fluent Icons", "Segoe MDL2 Assets"'

# Current tokens. Rebound by set_mode; import the module, not the names, if the value is
# needed at call time rather than at import time.
BG = SURFACE = SURFACE_2 = SURFACE_3 = BORDER = BORDER_SOFT = ""
TEXT = TEXT_MUTED = TEXT_FAINT = ""
ACCENT = ACCENT_HOVER = ACCENT_DIM = ON_ACCENT = ""
SUCCESS = WARNING = DANGER = FREE = SCROLL = SCROLL_HOVER = ""
MODE = "dark"


def tint(hex_colour: str, alpha: float, over: str | None = None) -> str:
    """A soft version of a token, returned as a SOLID hex.

    Two Qt facts make translucency the wrong tool. `#RRGGBBAA` is CSS; Qt reads eight
    digits as `#AARRGGBB`, which once turned a 13% warning tint into solid maroon. And
    Qt's `rgba()` rejects a 0.0-1.0 float alpha, so the fix for that came out fully
    opaque gold. Mixing up front sidesteps both.
    """
    background = over or SURFACE or "#ffffff"

    def channels(value: str):
        value = value.lstrip("#")
        if len(value) != 6:
            return (255, 255, 255)
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))

    weight = max(0.0, min(1.0, alpha))
    front, back = channels(hex_colour), channels(background)
    mixed = tuple(round(f * weight + b * (1 - weight)) for f, b in zip(front, back))
    return "#{:02x}{:02x}{:02x}".format(*mixed)


def restyle_tree(root) -> int:
    """Re-applies the current palette to a whole widget tree.

    Setting a new stylesheet on the window is not enough: a widget that set its own
    colours with `setStyleSheet` keeps them for ever. Widgets that do that expose
    `restyle()`; everything else is unpolished and repolished so Qt drops what it cached.
    """
    from PySide6.QtWidgets import QWidget

    touched = 0
    for widget in [root] + root.findChildren(QWidget):
        hook = getattr(widget, "restyle", None)
        if callable(hook):
            try:
                hook()
                touched += 1
            except Exception:
                pass
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
        widget.update()
    return touched


def _template(p: dict[str, str]) -> str:
    return f"""
* {{
    font-family: {FONT_STACK};
    font-size: 13px;
    color: {p['onSurface']};
}}

QMainWindow, QDialog {{ background: {p['surface']}; }}
QWidget {{ background: transparent; }}
QToolTip {{
    background: {p['inverseSurface']}; color: {p['inverseOnSurface']};
    border: none; border-radius: {SHAPE_XS}px; padding: 6px 8px; font-size: 12px;
}}

/* ---------------------------------------------------------------- typography ---- */
QLabel#display    {{ font-family: {FONT_DISPLAY}; font-size: 28px; font-weight: 600; }}
QLabel#h1         {{ font-family: {FONT_DISPLAY}; font-size: 21px; font-weight: 600;
                     color: {p['onSurface']}; padding: 0px; }}
QLabel#h2         {{ font-size: 15px; font-weight: 600; color: {p['onSurface']}; }}
QLabel#cardTitle  {{ font-size: 11px; font-weight: 700; color: {p['onSurfaceVariant']};
                     letter-spacing: 0.6px; text-transform: uppercase;
                     padding-bottom: 2px; }}
QLabel#subtle     {{ color: {p['onSurfaceVariant']}; }}
QLabel#faint      {{ color: {p['outline']}; font-size: 12px; }}
QLabel#okText     {{ color: {p['success']}; }}
QLabel#warnText   {{ color: {p['warning']}; }}
QLabel#dangerText {{ color: {p['error']}; }}
QLabel#mono       {{ color: {p['outline']};
                     font-family: "Roboto Mono", Consolas, monospace; font-size: 12px; }}
QLabel#glyph      {{ font-family: {ICON_FONT}; font-size: 22px;
                     color: {p['onSurfaceVariant']}; }}
QLabel#freeBadge  {{
    background: {p['successContainer']}; color: {p['onSuccessContainer']};
    border-radius: {SHAPE_SM}px; padding: 3px 10px;
    font-size: 11px; font-weight: 600;
}}

/* --------------------------------------------------------------------- cards ---- */
/* Depth comes from tone, not outlines: a card is one step lighter than the page it sits
   on. That is what stops the window reading as a stack of bordered boxes. */
QFrame#card {{
    background: {p['surfaceContainerLow']};
    border: none; border-radius: {SHAPE_MD}px;
}}
QFrame#cardRaised {{
    background: {p['surfaceContainer']};
    border: none; border-radius: {SHAPE_MD}px;
}}

/* ------------------------------------------------------------------- buttons ---- */
QPushButton {{
    background: {p['surfaceContainerHigh']};
    border: none; border-radius: {SHAPE_FULL}px;
    padding: 0px 14px; min-height: {SIZE_CONTROL}px;
    color: {p['onSurface']}; font-size: 13px; font-weight: 600;
}}
QPushButton:hover   {{ background: {p['surfaceContainerHighest']}; }}
QPushButton:pressed {{ background: {p['outlineVariant']}; }}
QPushButton:disabled {{ background: {p['surfaceContainer']}; color: {p['outline']}; }}

QPushButton#primary {{ background: {p['primary']}; color: {p['onPrimary']}; }}
QPushButton#primary:hover    {{ background: {p['primaryHover']}; }}
QPushButton#primary:pressed  {{ background: {p['primaryHover']}; }}
QPushButton#primary:disabled {{
    background: {p['surfaceContainerHigh']}; color: {p['outline']};
}}

QPushButton#tonal {{
    background: {p['secondaryContainer']}; color: {p['onSecondaryContainer']};
}}
QPushButton#tonal:hover {{ background: {p['primaryContainer']}; }}

QPushButton#ghost {{
    background: transparent; color: {p['primary']};
    border: 1px solid {p['outlineVariant']};
}}
QPushButton#ghost:hover {{ background: {p['surfaceContainer']}; }}
QPushButton#ghost:disabled, QPushButton#text:disabled {{
    color: {p['outline']}; border-color: {p['outlineVariant']}; background: transparent;
}}

QPushButton#text {{ background: transparent; color: {p['primary']}; padding: 0px 12px; }}
QPushButton#text:hover {{ background: {p['surfaceContainer']}; }}

QPushButton#iconButton {{
    background: transparent; border: none; border-radius: {SHAPE_FULL}px;
    padding: 0px; margin: 0px; min-height: {SIZE_CONTROL}px; min-width: {SIZE_CONTROL}px;
    font-family: {ICON_FONT}; font-size: 15px; color: {p['onSurfaceVariant']};
}}
QPushButton#iconButton:hover {{
    background: {p['surfaceContainerHigh']}; color: {p['onSurface']};
}}

/* ---------------------------------------------------------- navigation drawer ---- */
QWidget#sidebar {{ background: {p['surfaceContainerLow']}; border: none; }}
QPushButton#navItem {{
    background: transparent; border: none; border-radius: {SHAPE_FULL}px;
    padding: 0px 10px; min-height: {SIZE_ROW}px;
    text-align: left; font-size: 13px; font-weight: 500;
    color: {p['onSurfaceVariant']};
}}
QPushButton#navItem:hover   {{ background: {p['surfaceContainerHigh']}; }}
QPushButton#navItem:checked {{
    background: {p['secondaryContainer']}; color: {p['onSecondaryContainer']};
    font-weight: 600;
}}
QLabel#navSection {{
    color: {p['outline']}; font-size: 10px; font-weight: 700;
    letter-spacing: 1.0px; padding: 10px 10px 2px 10px;
}}
QLabel#brand {{
    font-family: {FONT_DISPLAY}; font-size: 16px; font-weight: 600;
    color: {p['onSurface']}; padding: 2px 4px 2px 8px;
}}

/* ------------------------------------------------------------------- stepper ---- */
/* A step is a number and a name. Current is a filled pill, finished is tinted, ahead is
   quiet -- so where you are and how far you have to go are both readable at a glance. */
QWidget#stepHeader {{
    background: {p['surfaceContainerLow']};
    border-bottom: 1px solid {p['outlineVariant']};
}}
QWidget#stepBar {{
    background: {p['surfaceContainerLow']};
    border-top: 1px solid {p['outlineVariant']};
}}
QPushButton#stepDot {{
    background: transparent; border: none; border-radius: {SHAPE_FULL}px;
    padding: 0px 14px; min-height: {SIZE_CONTROL}px;
    color: {p['outline']}; font-size: 13px; font-weight: 600;
}}
QPushButton#stepDot:hover {{ background: {p['surfaceContainerHigh']}; }}
QPushButton#stepDot[done="true"] {{ color: {p['primary']}; }}
QPushButton#stepDot:checked {{
    background: {p['primary']}; color: {p['onPrimary']}; font-weight: 600;
}}
QFrame#stepLine {{ background: {p['outlineVariant']}; border: none; max-height: 1px; }}

/* -------------------------------------------------------------------- inputs ---- */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTextEdit, QPlainTextEdit {{
    background: {p['surfaceContainerLowest']};
    border: 1px solid {p['outline']};
    border-radius: {SHAPE_XS}px;
    padding: 5px 10px; min-height: 20px; min-width: 88px;
    color: {p['onSurface']};
    selection-background-color: {p['primaryContainer']};
    selection-color: {p['onPrimaryContainer']};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
QTextEdit:focus, QPlainTextEdit:focus {{
    border: 2px solid {p['primary']}; padding: 7px 11px;
}}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{
    background: {p['surfaceContainer']}; color: {p['outline']};
    border-color: {p['outlineVariant']};
}}
/* The arrows are left to the native style. Faking a triangle with CSS borders needs a
   zero-sized box with `image: none`, and the windows11 style draws that as a small solid
   square instead -- worse than the arrow it replaced. */
QComboBox::drop-down {{ border: none; width: 28px; background: transparent; }}
QComboBox QAbstractItemView {{
    background: {p['surfaceContainerHigh']};
    border: none; border-radius: {SHAPE_SM}px; padding: 4px;
    selection-background-color: {p['secondaryContainer']};
    selection-color: {p['onSecondaryContainer']};
    outline: none;
}}
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    background: transparent; border: none; width: 22px;
}}

/* ------------------------------------------------------------------ selection ---- */
QCheckBox, QRadioButton {{ spacing: 10px; color: {p['onSurface']}; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 18px; height: 18px; }}
QCheckBox::indicator {{
    border: 2px solid {p['onSurfaceVariant']}; border-radius: {SHAPE_XS}px;
    background: transparent;
}}
QCheckBox::indicator:checked {{ background: {p['primary']}; border-color: {p['primary']}; }}
QRadioButton::indicator {{ border: 2px solid {p['onSurfaceVariant']}; border-radius: 9px; }}
QRadioButton::indicator:checked {{
    border: 5px solid {p['primary']}; background: {p['surfaceContainerLowest']};
}}

/* -------------------------------------------------------------------- slider ---- */
QSlider::groove:horizontal {{
    height: 4px; border-radius: 2px; background: {p['surfaceContainerHighest']};
}}
QSlider::sub-page:horizontal {{ background: {p['primary']}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: {p['primary']}; width: 16px; height: 16px;
    margin: -6px 0; border-radius: 8px;
}}
QSlider::handle:horizontal:hover {{ background: {p['primaryHover']}; }}

/* ---------------------------------------------------------------------- tabs ---- */
QTabWidget::pane {{ border: none; background: transparent; }}
QTabBar::tab {{
    background: transparent; border: none;
    padding: 8px 14px; margin-right: 2px;
    color: {p['onSurfaceVariant']}; font-size: 13px; font-weight: 600;
    border-bottom: 3px solid transparent;
}}
QTabBar::tab:hover    {{ color: {p['onSurface']}; }}
QTabBar::tab:selected {{ color: {p['primary']}; border-bottom: 3px solid {p['primary']}; }}

/* --------------------------------------------------------------------- lists ---- */
QListWidget, QTreeWidget, QTableWidget {{
    background: {p['surfaceContainerLowest']};
    border: 1px solid {p['outlineVariant']}; border-radius: {SHAPE_MD}px;
    outline: none; padding: 4px;
}}
QListWidget::item, QTreeWidget::item {{
    border-radius: {SHAPE_SM}px; padding: 8px; margin: 1px 2px;
    color: {p['onSurface']};
}}
QListWidget::item:hover, QTreeWidget::item:hover {{
    background: {p['surfaceContainerHigh']};
}}
QListWidget::item:selected, QTreeWidget::item:selected {{
    background: {p['secondaryContainer']}; color: {p['onSecondaryContainer']};
}}
QTableWidget::item {{ padding: 6px; color: {p['onSurface']}; }}
QTableWidget::item:selected {{
    background: {p['secondaryContainer']}; color: {p['onSecondaryContainer']};
}}
QHeaderView::section {{
    background: transparent; border: none;
    border-bottom: 1px solid {p['outlineVariant']};
    padding: 8px 6px; color: {p['onSurfaceVariant']};
    font-size: 12px; font-weight: 600;
}}

/* ---------------------------------------------------------------- scrollbars ---- */
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 0px; }}
QScrollBar::handle:vertical {{
    background: {p['scrim']}; border-radius: 6px; min-height: 40px; margin: 2px 3px;
}}
QScrollBar::handle:vertical:hover {{ background: {p['scrimHover']}; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 0px; }}
QScrollBar::handle:horizontal {{
    background: {p['scrim']}; border-radius: 6px; min-width: 40px; margin: 3px 2px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0px; width: 0px; border: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ------------------------------------------------------------------ splitter ---- */
/* The handle has to look grabbable, or "the panels cannot be moved" is a fair complaint
   even when they can. */
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:horizontal {{ width: 12px; image: none; }}
QSplitter::handle:horizontal:hover {{ background: {p['primaryContainer']}; }}
QSplitter::handle:vertical {{ height: 12px; }}
QSplitter::handle:vertical:hover {{ background: {p['primaryContainer']}; }}

/* --------------------------------------------------------------------- other ---- */
QProgressBar {{
    background: {p['surfaceContainerHighest']}; border: none;
    border-radius: 2px; height: 4px; text-align: center; color: transparent;
}}
QProgressBar::chunk {{ background: {p['primary']}; border-radius: 2px; }}

QMenuBar {{ background: {p['surface']}; color: {p['onSurface']}; padding: 4px; }}
QMenuBar::item {{ padding: 6px 12px; border-radius: {SHAPE_SM}px; background: transparent; }}
QMenuBar::item:selected {{ background: {p['surfaceContainerHigh']}; }}
QMenu {{
    background: {p['surfaceContainerHigh']}; border: none;
    border-radius: {SHAPE_SM}px; padding: 6px;
}}
QMenu::item {{ padding: 8px 24px 8px 14px; border-radius: {SHAPE_XS}px; }}
QMenu::item:selected {{
    background: {p['secondaryContainer']}; color: {p['onSecondaryContainer']};
}}
QMenu::separator {{ height: 1px; background: {p['outlineVariant']}; margin: 6px 8px; }}

QStatusBar {{ background: {p['surfaceContainerLow']}; color: {p['onSurfaceVariant']}; }}
QStatusBar::item {{ border: none; }}

QScrollArea {{ background: transparent; border: none; }}
QGroupBox {{
    border: 1px solid {p['outlineVariant']}; border-radius: {SHAPE_MD}px;
    margin-top: 10px; padding-top: 10px;
}}
"""


def set_mode(mode: str) -> str:
    """Rebinds the module tokens and returns the stylesheet for that mode."""
    global MODE, BG, SURFACE, SURFACE_2, SURFACE_3, BORDER, BORDER_SOFT
    global TEXT, TEXT_MUTED, TEXT_FAINT, ACCENT, ACCENT_HOVER, ACCENT_DIM, ON_ACCENT
    global SUCCESS, WARNING, DANGER, FREE, SCROLL, SCROLL_HOVER, STYLESHEET

    palette = PALETTES.get(mode) or PALETTES["dark"]
    MODE = mode if mode in PALETTES else "dark"

    BG, SURFACE, SURFACE_2 = palette["BG"], palette["SURFACE"], palette["SURFACE_2"]
    SURFACE_3, BORDER, BORDER_SOFT = (palette["SURFACE_3"], palette["BORDER"],
                                      palette["BORDER_SOFT"])
    TEXT, TEXT_MUTED, TEXT_FAINT = (palette["TEXT"], palette["TEXT_MUTED"],
                                    palette["TEXT_FAINT"])
    ACCENT, ACCENT_HOVER = palette["ACCENT"], palette["ACCENT_HOVER"]
    ACCENT_DIM, ON_ACCENT = palette["ACCENT_DIM"], palette["ON_ACCENT"]
    SUCCESS, WARNING, DANGER, FREE = (palette["SUCCESS"], palette["WARNING"],
                                      palette["DANGER"], palette["FREE"])
    SCROLL, SCROLL_HOVER = palette["SCROLL"], palette["SCROLL_HOVER"]

    STYLESHEET = _template(palette)
    return STYLESHEET


def badge(text: str, colour: str) -> str:
    """A small inline pill for rich-text labels.

    The background is blended rather than given an alpha suffix: `{colour}22` was read by
    Qt as `#AARRGGBB` and produced a muddy slab instead of a tint.
    """
    return (
        f"<span style='background:{tint(colour, 0.16)}; color:{colour}; "
        f"border-radius:{SHAPE_SM}px; padding:1px 8px; "
        f"font-size:11px; font-weight:600'>{text}</span>"
    )


def stylesheet(mode: str | None = None) -> str:
    return set_mode(mode) if mode else STYLESHEET


STYLESHEET = set_mode("dark")
