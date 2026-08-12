"""Themeable QSS (dark/light/high-contrast x compact/comfortable/spacious),
in the spirit of Kdenlive's default look (clean-room QSS - no Kdenlive code
or assets are used; PrismCut is MIT, Kdenlive is GPL).

Color/spacing constants below are set as *live module attributes* by
set_theme() - other modules (timeline.py, photo_studio.py) read them via
``from .. import theme`` then ``theme.ACCENT`` etc. at paint time, so a
theme switch repaints them with zero changes on their end.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

# "dark" keeps every literal value the app already shipped with, so a user
# who never touches theme/density settings sees pixel-identical output.
_PALETTES = {
    "dark": {
        "BG": "#1e2124", "PANEL": "#26292d", "PANEL_ALT": "#2c3036", "BORDER": "#3a3f46",
        "TEXT": "#d6d9dd", "TEXT_DIM": "#8b929b", "ACCENT": "#29b6f6", "ACCENT_DARK": "#0288d1",
        "ORANGE": "#ffa726", "CLIP_VIDEO": "#3d6b8e", "CLIP_AUDIO": "#3d8e6b", "DANGER": "#ef5350",
    },
    "light": {
        "BG": "#f4f5f7", "PANEL": "#ffffff", "PANEL_ALT": "#e9ebee", "BORDER": "#d0d4da",
        "TEXT": "#20242a", "TEXT_DIM": "#6b7280", "ACCENT": "#0288d1", "ACCENT_DARK": "#01579b",
        "ORANGE": "#e65100", "CLIP_VIDEO": "#5b8fb5", "CLIP_AUDIO": "#4c9b78", "DANGER": "#c62828",
    },
    "high_contrast": {
        "BG": "#000000", "PANEL": "#121212", "PANEL_ALT": "#1e1e1e", "BORDER": "#8a8a8a",
        "TEXT": "#ffffff", "TEXT_DIM": "#d0d0d0", "ACCENT": "#00e5ff", "ACCENT_DARK": "#00b8d4",
        "ORANGE": "#ffab00", "CLIP_VIDEO": "#4fc3f7", "CLIP_AUDIO": "#69f0ae", "DANGER": "#ff1744",
    },
}

THEME_LABELS = {"dark": "Dark", "light": "Light", "high_contrast": "High contrast"}

# "comfortable" keeps every literal spacing/font value the app already
# shipped with.
_DENSITY = {
    "compact": {
        "font_px": 11, "btn_pad": "3px 10px", "tool_pad": "2px 5px",
        "menubar_pad": "3px 8px", "menu_pad": "3px 20px 3px 10px", "tab_pad": "3px 10px",
        "field_pad": "2px 4px", "item_pad": "1px", "header_pad": "2px 4px",
    },
    "comfortable": {
        "font_px": 12, "btn_pad": "6px 14px", "tool_pad": "4px 7px",
        "menubar_pad": "5px 10px", "menu_pad": "5px 24px 5px 12px", "tab_pad": "6px 14px",
        "field_pad": "4px 6px", "item_pad": "3px", "header_pad": "4px 6px",
    },
    "spacious": {
        "font_px": 13, "btn_pad": "9px 18px", "tool_pad": "7px 10px",
        "menubar_pad": "8px 14px", "menu_pad": "8px 28px 8px 16px", "tab_pad": "9px 18px",
        "field_pad": "7px 9px", "item_pad": "6px", "header_pad": "7px 9px",
    },
}

DENSITY_LABELS = {"compact": "Compact", "comfortable": "Comfortable", "spacious": "Spacious"}

DEFAULT_THEME = "dark"
DEFAULT_DENSITY = "comfortable"

# Populated/overwritten by set_theme() - declared here (dark defaults) so
# `theme.ACCENT`/`theme.CURRENT_THEME` etc. are always valid attributes even
# if something constructs UI before set_theme()/apply_theme() ever runs
# (app.py always calls apply_theme() before any window is constructed, so
# in practice these are overwritten immediately in the real app).
globals().update(_PALETTES[DEFAULT_THEME])
CURRENT_THEME = DEFAULT_THEME
CURRENT_DENSITY = DEFAULT_DENSITY


def _build_qss(pal: dict, dens: dict) -> str:
    return f"""
* {{ outline: none; }}
QWidget {{ background: {pal['BG']}; color: {pal['TEXT']}; font-size: {dens['font_px']}px; }}
QMainWindow::separator {{ background: {pal['BORDER']}; width: 3px; height: 3px; }}

QMenuBar {{ background: {pal['PANEL']}; border-bottom: 1px solid {pal['BORDER']}; }}
QMenuBar::item {{ padding: {dens['menubar_pad']}; background: transparent; }}
QMenuBar::item:selected {{ background: {pal['PANEL_ALT']}; border-radius: 4px; }}
QMenu {{ background: {pal['PANEL']}; border: 1px solid {pal['BORDER']}; padding: 4px; }}
QMenu::item {{ padding: {dens['menu_pad']}; border-radius: 4px; }}
QMenu::item:selected {{ background: {pal['ACCENT_DARK']}; }}
QMenu::separator {{ height: 1px; background: {pal['BORDER']}; margin: 4px 8px; }}

QToolBar {{ background: {pal['PANEL']}; border: none; spacing: 3px; padding: 3px; }}
QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 4px;
               padding: {dens['tool_pad']}; color: {pal['TEXT']}; }}
QToolButton:hover {{ background: {pal['PANEL_ALT']}; border-color: {pal['BORDER']}; }}
QToolButton:checked {{ background: {pal['ACCENT_DARK']}; border-color: {pal['ACCENT']}; color: white; }}
QToolButton:disabled {{ color: {pal['TEXT_DIM']}; }}
QToolButton:focus {{ border: 2px solid {pal['ACCENT']}; }}

QDockWidget {{ titlebar-close-icon: none; titlebar-normal-icon: none; color: {pal['TEXT']}; }}
QDockWidget::title {{ background: {pal['PANEL']}; padding: 5px 8px; border-bottom: 1px solid {pal['BORDER']};
                      font-weight: bold; }}

QTabWidget::pane {{ border: 1px solid {pal['BORDER']}; background: {pal['PANEL']}; }}
QTabBar::tab {{ background: {pal['BG']}; color: {pal['TEXT_DIM']}; padding: {dens['tab_pad']};
                border: 1px solid {pal['BORDER']}; border-bottom: none;
                border-top-left-radius: 5px; border-top-right-radius: 5px; margin-right: 2px; }}
QTabBar::tab:selected {{ background: {pal['PANEL']}; color: {pal['TEXT']};
                         border-bottom: 2px solid {pal['ACCENT']}; }}
QTabBar::tab:hover {{ color: {pal['TEXT']}; }}
QTabBar::tab:focus {{ border-bottom: 2px solid {pal['ACCENT']}; }}

QPushButton {{ background: {pal['PANEL_ALT']}; border: 1px solid {pal['BORDER']}; border-radius: 5px;
               padding: {dens['btn_pad']}; color: {pal['TEXT']}; }}
QPushButton:hover {{ border-color: {pal['ACCENT']}; }}
QPushButton:pressed {{ background: {pal['BG']}; }}
QPushButton:disabled {{ color: {pal['TEXT_DIM']}; border-color: {pal['BORDER']}; }}
QPushButton:focus {{ border: 2px solid {pal['ACCENT']}; }}
QPushButton[accent="true"] {{ background: {pal['ACCENT_DARK']}; border-color: {pal['ACCENT']};
                              color: white; font-weight: bold; }}
QPushButton[accent="true"]:hover {{ background: {pal['ACCENT']}; }}
QPushButton[danger="true"] {{ background: transparent; border-color: {pal['DANGER']}; color: {pal['DANGER']}; }}

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {pal['BG']}; border: 1px solid {pal['BORDER']}; border-radius: 4px; padding: {dens['field_pad']};
    selection-background-color: {pal['ACCENT_DARK']}; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus {{ border-color: {pal['ACCENT']}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{ background: {pal['PANEL']}; border: 1px solid {pal['BORDER']};
                               selection-background-color: {pal['ACCENT_DARK']}; }}

QSlider::groove:horizontal {{ height: 4px; background: {pal['BORDER']}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {pal['ACCENT']}; width: 12px; height: 12px;
                              margin: -5px 0; border-radius: 6px; }}
QSlider::groove:vertical {{ width: 4px; background: {pal['BORDER']}; border-radius: 2px; }}
QSlider::handle:vertical {{ background: {pal['ACCENT']}; height: 12px; margin: 0 -5px; border-radius: 6px; }}
QSlider::handle:focus {{ border: 2px solid {pal['TEXT']}; }}

QScrollBar:vertical {{ background: {pal['BG']}; width: 11px; }}
QScrollBar::handle:vertical {{ background: {pal['BORDER']}; border-radius: 5px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {pal['TEXT_DIM']}; }}
QScrollBar:horizontal {{ background: {pal['BG']}; height: 11px; }}
QScrollBar::handle:horizontal {{ background: {pal['BORDER']}; border-radius: 5px; min-width: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}

QTreeWidget, QListWidget, QTableWidget {{ background: {pal['PANEL']}; border: 1px solid {pal['BORDER']};
    alternate-background-color: {pal['PANEL_ALT']}; }}
QTreeWidget::item, QListWidget::item {{ padding: {dens['item_pad']}; border-radius: 3px; }}
QTreeWidget::item:selected, QListWidget::item:selected {{ background: {pal['ACCENT_DARK']}; color: white; }}
QTreeWidget::item:focus, QListWidget::item:focus {{ outline: 1px solid {pal['ACCENT']}; outline-offset: -1px; }}
QHeaderView::section {{ background: {pal['PANEL_ALT']}; border: none; border-right: 1px solid {pal['BORDER']};
    padding: {dens['header_pad']}; }}

QGroupBox {{ border: 1px solid {pal['BORDER']}; border-radius: 6px; margin-top: 12px;
             font-weight: bold; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; color: {pal['ACCENT']}; }}

QProgressBar {{ background: {pal['BG']}; border: 1px solid {pal['BORDER']}; border-radius: 4px;
                text-align: center; height: 14px; }}
QProgressBar::chunk {{ background: {pal['ACCENT_DARK']}; border-radius: 3px; }}

QStatusBar {{ background: {pal['PANEL']}; border-top: 1px solid {pal['BORDER']}; color: {pal['TEXT_DIM']}; }}
QToolTip {{ background: {pal['PANEL_ALT']}; color: {pal['TEXT']}; border: 1px solid {pal['ACCENT']};
            padding: 4px 6px; }}
QSplitter::handle {{ background: {pal['BORDER']}; }}
QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid {pal['BORDER']};
    border-radius: 3px; background: {pal['BG']}; }}
QCheckBox::indicator:checked {{ background: {pal['ACCENT_DARK']}; border-color: {pal['ACCENT']}; }}
QCheckBox:focus {{ border: 1px solid {pal['ACCENT']}; border-radius: 3px; }}
QLabel[dim="true"] {{ color: {pal['TEXT_DIM']}; }}
QLabel[h1="true"] {{ font-size: {dens['font_px'] + 3}px; font-weight: bold; color: {pal['TEXT']}; }}

Toast {{ background: {pal['PANEL_ALT']}; border: 1px solid {pal['BORDER']}; border-radius: 6px; }}
Toast[kind="error"] {{ border-color: {pal['DANGER']}; }}
Toast[kind="success"] {{ border-color: {pal['ACCENT']}; }}

QLabel#audioWave {{ background: {pal['BG']}; border: 1px solid {pal['BORDER']}; }}
QTextBrowser#chatView {{ background: {pal['BG']}; border: 1px solid {pal['BORDER']}; padding: 6px; }}
QFrame#hLine {{ color: {pal['BORDER']}; }}
QWidget#toastOverlay {{ background: transparent; }}
QWidget#timelineCorner {{ background: {pal['PANEL']}; border-bottom: 1px solid {pal['BORDER']}; }}
"""


def _qpalette(pal: dict) -> QPalette:
    qp = QPalette()
    qp.setColor(QPalette.ColorRole.Window, QColor(pal["BG"]))
    qp.setColor(QPalette.ColorRole.WindowText, QColor(pal["TEXT"]))
    qp.setColor(QPalette.ColorRole.Base, QColor(pal["PANEL"]))
    qp.setColor(QPalette.ColorRole.AlternateBase, QColor(pal["PANEL_ALT"]))
    qp.setColor(QPalette.ColorRole.Text, QColor(pal["TEXT"]))
    qp.setColor(QPalette.ColorRole.Button, QColor(pal["PANEL_ALT"]))
    qp.setColor(QPalette.ColorRole.ButtonText, QColor(pal["TEXT"]))
    qp.setColor(QPalette.ColorRole.Highlight, QColor(pal["ACCENT_DARK"]))
    qp.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    qp.setColor(QPalette.ColorRole.ToolTipBase, QColor(pal["PANEL_ALT"]))
    qp.setColor(QPalette.ColorRole.ToolTipText, QColor(pal["TEXT"]))
    qp.setColor(QPalette.ColorRole.Link, QColor(pal["ACCENT"]))
    return qp


def set_theme(app: QApplication, theme_name: str, density: str = DEFAULT_DENSITY) -> None:
    theme_name = theme_name if theme_name in _PALETTES else DEFAULT_THEME
    density = density if density in _DENSITY else DEFAULT_DENSITY
    pal = _PALETTES[theme_name]
    globals().update(pal)
    globals()["CURRENT_THEME"] = theme_name
    globals()["CURRENT_DENSITY"] = density
    app.setPalette(_qpalette(pal))
    app.setStyleSheet(_build_qss(pal, _DENSITY[density]))


def system_prefers_light() -> bool:
    try:
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Light
    except Exception:  # pragma: no cover - very old Qt without colorScheme()
        return False


def apply_theme(app: QApplication, settings=None) -> None:
    """Called once at startup. If the user has never chosen a theme,
    default follows the OS light/dark preference; an explicit prior choice
    always wins."""
    app.setStyle("Fusion")
    theme_name = settings.get("ui/theme") if settings else None
    if not theme_name:
        theme_name = "light" if system_prefers_light() else "dark"
    density = (settings.get("ui/density", DEFAULT_DENSITY) if settings else DEFAULT_DENSITY)
    set_theme(app, str(theme_name), str(density))
