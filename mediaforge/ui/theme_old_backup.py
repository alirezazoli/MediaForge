"""Visual design system, with a light and a dark palette.

Panels must not hardcode hex values. They read the module-level tokens, and anything that
sets an inline style has to be rebuilt when `set_mode` changes them -- panels do that in
their own `refresh`, which the main window calls after a theme switch.
"""

from __future__ import annotations

RADIUS = 10
RADIUS_SM = 7

PALETTES: dict[str, dict[str, str]] = {
    "dark": {
        "BG": "#0f1115",
        "SURFACE": "#171a20",
        "SURFACE_2": "#1e222a",
        "SURFACE_3": "#262b34",
        "BORDER": "#272c36",
        "BORDER_SOFT": "#20242c",
        "TEXT": "#e8eaed",
        "TEXT_MUTED": "#9aa3b0",
        "TEXT_FAINT": "#68707d",
        "ACCENT": "#5b8cff",
        "ACCENT_HOVER": "#6f9bff",
        "ACCENT_DIM": "#2b3f73",
        "ON_ACCENT": "#0b1020",
        "SUCCESS": "#3fb950",
        "WARNING": "#d29922",
        "DANGER": "#f0616d",
        "FREE": "#3fb950",
        "SCROLL": "#2c323c",
        "SCROLL_HOVER": "#3a4250",
    },
    "light": {
        "BG": "#f4f6f9",
        "SURFACE": "#ffffff",
        "SURFACE_2": "#f0f2f6",
        "SURFACE_3": "#e4e8ef",
        "BORDER": "#d3d9e2",
        "BORDER_SOFT": "#e2e6ed",
        "TEXT": "#1a1d23",
        "TEXT_MUTED": "#5c6673",
        "TEXT_FAINT": "#8b95a3",
        "ACCENT": "#2f6feb",
        "ACCENT_HOVER": "#2861d6",
        "ACCENT_DIM": "#cddcfb",
        "ON_ACCENT": "#ffffff",
        "SUCCESS": "#18794e",
        "WARNING": "#9a6700",
        "DANGER": "#cf222e",
        "FREE": "#18794e",
        "SCROLL": "#c6ccd6",
        "SCROLL_HOVER": "#aab2bf",
    },
}

MODES = list(PALETTES)

# Current tokens. Rebound by set_mode; import the module, not the names, if you need
# values at call time rather than at import time.
BG = SURFACE = SURFACE_2 = SURFACE_3 = BORDER = BORDER_SOFT = ""
TEXT = TEXT_MUTED = TEXT_FAINT = ""
ACCENT = ACCENT_HOVER = ACCENT_DIM = ON_ACCENT = ""
SUCCESS = WARNING = DANGER = FREE = SCROLL = SCROLL_HOVER = ""
MODE = "dark"


def _template(p: dict[str, str]) -> str:
    return f"""
* {{
    font-family: "Segoe UI Variable Display", "Segoe UI", Inter, system-ui, sans-serif;
    font-size: 13px;
}}

QMainWindow, QDialog {{ background: {p['BG']}; }}

/* A combo sized to its longest entry ("Fill the frame (crop edges)") sets a minimum
   width that the whole column then cannot go below, so a narrow column clips instead of
   compressing. Letting these shrink is what keeps three columns from overlapping. */
/* 60px let a combo shrink until its own contents read "Don't c". Forms give these a
   row to themselves now, so the floor can be a width that actually shows a word. */
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {{ min-width: 96px; }}
QWidget {{ color: {p['TEXT']}; background: transparent; }}

QFrame#card {{
    background: {p['SURFACE']};
    border: 1px solid {p['BORDER_SOFT']};
    border-radius: {RADIUS}px;
}}
QLabel#cardTitle {{
    font-size: 12px; font-weight: 700; color: {p['TEXT_MUTED']};
    letter-spacing: 0.6px; text-transform: uppercase; padding-bottom: 4px;
}}
/* Headings were rendering close to the muted body colour, which left the screen with
   no visible top of the hierarchy. They get the full text colour and real air. */
QLabel#h1 {{ font-size: 26px; font-weight: 700; color: {p['TEXT']};
             padding: 2px 0 2px 0; }}
QLabel#h2 {{ font-size: 17px; font-weight: 650; color: {p['TEXT']}; }}
QLabel#subtle {{ color: {p['TEXT_MUTED']}; }}
/* Named states instead of inline hex. A colour written into a widget's own stylesheet
   survives a theme change and leaves last theme's green sitting on this theme's white;
   a class name is re-resolved every time the palette is rebuilt. */
QLabel#okText     {{ color: {p['FREE']}; }}
QLabel#warnText   {{ color: {p['WARNING']}; }}
QLabel#dangerText {{ color: {p['DANGER']}; }}
QLabel#freeBadge {{
    background: {p['ACCENT_DIM']}; color: {p['FREE']}; border-radius: 6px;
    padding: 2px 8px; font-size: 10px; font-weight: 700;
}}
QLabel#glyph {{ font-size: 26px; }}
QLabel#mono {{ color: {p['TEXT_FAINT']}; font-family: Consolas, "Cascadia Mono", monospace; }}
QLabel#faint  {{ color: {p['TEXT_FAINT']}; }}

QPushButton {{
    background: {p['SURFACE_2']}; border: 1px solid {p['BORDER']};
    border-radius: {RADIUS_SM}px; padding: 7px 14px; color: {p['TEXT']};
}}
QPushButton:hover {{ background: {p['SURFACE_3']}; }}
QPushButton:pressed {{ background: {p['BORDER']}; }}
QPushButton:disabled {{ color: {p['TEXT_FAINT']}; background: {p['BG']};
                        border-color: {p['BORDER_SOFT']}; }}

QPushButton#primary {{
    background: {p['ACCENT']}; border: 1px solid {p['ACCENT']};
    color: {p['ON_ACCENT']}; font-weight: 650; padding: 9px 20px;
}}
QPushButton#primary:hover {{ background: {p['ACCENT_HOVER']}; border-color: {p['ACCENT_HOVER']}; }}
QPushButton#primary:disabled {{ background: {p['ACCENT_DIM']}; border-color: {p['ACCENT_DIM']};
                                color: {p['TEXT_FAINT']}; }}

QPushButton#big {{
    background: {p['ACCENT']}; border: none; color: {p['ON_ACCENT']};
    font-size: 15px; font-weight: 700; padding: 14px 28px; border-radius: {RADIUS}px;
}}
QPushButton#big:hover {{ background: {p['ACCENT_HOVER']}; }}
QPushButton#big:disabled {{ background: {p['ACCENT_DIM']}; color: {p['TEXT_FAINT']}; }}

QPushButton#ghost {{ background: transparent; border: 1px solid {p['BORDER']}; }}
QPushButton#ghost:hover {{ background: {p['SURFACE_2']}; }}
QPushButton#danger {{ border-color: {p['DANGER']}55; color: {p['DANGER']}; }}
QPushButton#danger:hover {{ background: {p['DANGER']}18; }}

QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {p['SURFACE_2']}; border: 1px solid {p['BORDER']};
    border-radius: {RADIUS_SM}px; padding: 7px 9px;
    selection-background-color: {p['ACCENT']}; selection-color: {p['ON_ACCENT']};
}}
QLineEdit:focus, QTextEdit:focus, QComboBox:focus,
QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {p['ACCENT']}; }}
QLineEdit:disabled, QComboBox:disabled {{ color: {p['TEXT_FAINT']}; background: {p['BG']}; }}

QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{
    image: none; border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-top: 5px solid {p['TEXT_MUTED']}; margin-right: 8px;
}}
QComboBox QAbstractItemView {{
    background: {p['SURFACE_2']}; border: 1px solid {p['BORDER']};
    border-radius: {RADIUS_SM}px; selection-background-color: {p['ACCENT_DIM']};
    outline: none; padding: 4px;
}}
QComboBox QAbstractItemView::item {{ padding: 6px 8px; border-radius: 5px; }}
QComboBox QAbstractItemView::item:disabled {{ color: {p['TEXT_FAINT']}; }}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border: 1px solid {p['BORDER']};
    border-radius: 4px; background: {p['SURFACE_2']};
}}
QCheckBox::indicator:checked {{ background: {p['ACCENT']}; border-color: {p['ACCENT']}; }}

QSlider::groove:horizontal {{ height: 4px; background: {p['BORDER']}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: {p['ACCENT']}; width: 14px; height: 14px; margin: -6px 0; border-radius: 7px;
}}
QSlider::sub-page:horizontal {{ background: {p['ACCENT']}; border-radius: 2px; }}

QWidget#sidebar {{ background: {p['SURFACE']}; border-right: 1px solid {p['BORDER_SOFT']}; }}
QPushButton#navItem {{
    background: transparent; border: none; border-radius: {RADIUS_SM}px;
    padding: 9px 12px; text-align: left; color: {p['TEXT_MUTED']}; font-size: 13px;
}}
QPushButton#navItem:hover {{ background: {p['SURFACE_2']}; color: {p['TEXT']}; }}
QPushButton#navItem:checked {{ background: {p['ACCENT_DIM']}; color: {p['TEXT']}; font-weight: 600; }}
QLabel#navSection {{
    color: {p['TEXT_FAINT']}; font-size: 10px; font-weight: 700;
    letter-spacing: 1px; padding: 14px 12px 4px 12px;
}}
/* A 26px square button cannot also carry 14px of side padding -- the glyph gets no
   room and the button draws empty. */
QPushButton#iconButton {{
    background: transparent; border: 1px solid {p['BORDER']};
    border-radius: {RADIUS_SM}px; padding: 0px; margin: 0px;
    color: {p['TEXT_MUTED']}; font-weight: 700;
}}
QPushButton#iconButton:hover {{ background: {p['SURFACE_2']}; color: {p['TEXT']}; }}

QLabel#brand {{ font-size: 16px; font-weight: 700; color: {p['TEXT']}; padding: 14px 12px 2px; }}

QListWidget, QTableWidget, QTreeWidget, QListView {{
    background: {p['SURFACE']}; border: 1px solid {p['BORDER_SOFT']};
    border-radius: {RADIUS}px; outline: none;
}}
QListWidget::item {{ border-radius: {RADIUS_SM}px; padding: 4px; }}
QListWidget::item:selected, QListView::item:selected {{ background: {p['ACCENT_DIM']}; color: {p['TEXT']}; }}
QListWidget::item:hover {{ background: {p['SURFACE_2']}; }}
QTableWidget::item:selected {{ background: {p['ACCENT_DIM']}; color: {p['TEXT']}; }}
QHeaderView::section {{
    background: {p['SURFACE_2']}; color: {p['TEXT_MUTED']}; padding: 7px;
    border: none; border-bottom: 1px solid {p['BORDER']}; font-weight: 600;
}}

QProgressBar {{
    border: none; background: {p['SURFACE_2']}; border-radius: 5px;
    height: 8px; text-align: center; color: transparent;
}}
QProgressBar::chunk {{ background: {p['ACCENT']}; border-radius: 5px; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {p['SCROLL']}; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {p['SCROLL_HOVER']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {p['SCROLL']}; border-radius: 5px; min-width: 30px; }}

QSplitter::handle {{ background: transparent; }}
QSplitter::handle:hover {{ background: {p['ACCENT_DIM']}; }}
QSplitter::handle:horizontal {{ width: 8px; }}
QSplitter::handle:vertical {{ height: 8px; }}

QTabBar::tab {{
    background: transparent; color: {p['TEXT_MUTED']}; padding: 8px 16px;
    border: none; border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{ color: {p['TEXT']}; border-bottom: 2px solid {p['ACCENT']}; font-weight: 600; }}
QTabWidget::pane {{ border: none; }}

QMenuBar {{ background: {p['BG']}; border-bottom: 1px solid {p['BORDER_SOFT']}; }}
QMenuBar::item {{ padding: 6px 11px; background: transparent; border-radius: 5px; }}
QMenuBar::item:selected {{ background: {p['SURFACE_2']}; }}
QMenu {{
    background: {p['SURFACE_2']}; border: 1px solid {p['BORDER']};
    border-radius: {RADIUS_SM}px; padding: 5px;
}}
QMenu::item {{ padding: 6px 24px 6px 12px; border-radius: 5px; }}
QMenu::item:selected {{ background: {p['ACCENT_DIM']}; }}
QMenu::separator {{ height: 1px; background: {p['BORDER']}; margin: 4px 8px; }}

QStatusBar {{ background: {p['SURFACE']}; border-top: 1px solid {p['BORDER_SOFT']}; color: {p['TEXT_MUTED']}; }}
QToolTip {{
    background: {p['SURFACE_2']}; color: {p['TEXT']}; border: 1px solid {p['BORDER']};
    border-radius: 6px; padding: 6px;
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
    SURFACE_3, BORDER, BORDER_SOFT = palette["SURFACE_3"], palette["BORDER"], palette["BORDER_SOFT"]
    TEXT, TEXT_MUTED, TEXT_FAINT = palette["TEXT"], palette["TEXT_MUTED"], palette["TEXT_FAINT"]
    ACCENT, ACCENT_HOVER = palette["ACCENT"], palette["ACCENT_HOVER"]
    ACCENT_DIM, ON_ACCENT = palette["ACCENT_DIM"], palette["ON_ACCENT"]
    SUCCESS, WARNING, DANGER, FREE = (palette["SUCCESS"], palette["WARNING"],
                                      palette["DANGER"], palette["FREE"])
    SCROLL, SCROLL_HOVER = palette["SCROLL"], palette["SCROLL_HOVER"]

    STYLESHEET = _template(palette)
    return STYLESHEET


def tint(hex_colour: str, alpha: float, over: str | None = None) -> str:
    """A soft version of a token, returned as a SOLID hex.

    Two Qt facts make translucency the wrong tool here. `#RRGGBBAA` is CSS; Qt reads an
    8-digit hex as `#AARRGGBB`, which turned a 13% warning tint into solid maroon. And
    Qt's `rgba()` does not accept a 0.0-1.0 float alpha, so the "fixed" version came out
    fully opaque gold. Mixing the colour into the surface up front sidesteps both: the
    result is an ordinary hex that every Qt version paints identically.
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

    Setting a new stylesheet on the window is not enough. Any widget that set its own
    colours with `setStyleSheet` at construction keeps them for ever -- that is why the
    video preview stayed black after switching to the light theme. Widgets that do that
    expose `restyle()` to rebuild those strings, and everything else gets unpolished and
    repolished so Qt recomputes what it cached.
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


def stylesheet(mode: str | None = None) -> str:
    return set_mode(mode or MODE)


def badge(text: str, colour: str) -> str:
    return (
        f"<span style='background:{colour}22; color:{colour}; "
        f"border-radius:6px; padding:1px 7px; font-size:11px; font-weight:600'>{text}</span>"
    )


# Bind the default so importing the module always yields usable tokens.
STYLESHEET = set_mode("dark")
