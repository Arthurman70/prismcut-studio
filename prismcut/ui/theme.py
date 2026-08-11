"""Dark theme in the spirit of Kdenlive's default look (clean-room QSS - no
Kdenlive code or assets are used; PrismCut is MIT, Kdenlive is GPL)."""
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

BG = "#1e2124"
PANEL = "#26292d"
PANEL_ALT = "#2c3036"
BORDER = "#3a3f46"
TEXT = "#d6d9dd"
TEXT_DIM = "#8b929b"
ACCENT = "#29b6f6"
ACCENT_DARK = "#0288d1"
ORANGE = "#ffa726"
CLIP_VIDEO = "#3d6b8e"
CLIP_AUDIO = "#3d8e6b"
DANGER = "#ef5350"

QSS = f"""
* {{ outline: none; }}
QWidget {{ background: {BG}; color: {TEXT}; font-size: 12px; }}
QMainWindow::separator {{ background: {BORDER}; width: 3px; height: 3px; }}

QMenuBar {{ background: {PANEL}; border-bottom: 1px solid {BORDER}; }}
QMenuBar::item {{ padding: 5px 10px; background: transparent; }}
QMenuBar::item:selected {{ background: {PANEL_ALT}; border-radius: 4px; }}
QMenu {{ background: {PANEL}; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 5px 24px 5px 12px; border-radius: 4px; }}
QMenu::item:selected {{ background: {ACCENT_DARK}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 8px; }}

QToolBar {{ background: {PANEL}; border: none; spacing: 3px; padding: 3px; }}
QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 4px;
               padding: 4px 7px; color: {TEXT}; }}
QToolButton:hover {{ background: {PANEL_ALT}; border-color: {BORDER}; }}
QToolButton:checked {{ background: {ACCENT_DARK}; border-color: {ACCENT}; color: white; }}
QToolButton:disabled {{ color: {TEXT_DIM}; }}

QDockWidget {{ titlebar-close-icon: none; titlebar-normal-icon: none; color: {TEXT}; }}
QDockWidget::title {{ background: {PANEL}; padding: 5px 8px; border-bottom: 1px solid {BORDER};
                      font-weight: bold; }}

QTabWidget::pane {{ border: 1px solid {BORDER}; background: {PANEL}; }}
QTabBar::tab {{ background: {BG}; color: {TEXT_DIM}; padding: 6px 14px;
                border: 1px solid {BORDER}; border-bottom: none;
                border-top-left-radius: 5px; border-top-right-radius: 5px; margin-right: 2px; }}
QTabBar::tab:selected {{ background: {PANEL}; color: {TEXT};
                         border-bottom: 2px solid {ACCENT}; }}
QTabBar::tab:hover {{ color: {TEXT}; }}

QPushButton {{ background: {PANEL_ALT}; border: 1px solid {BORDER}; border-radius: 5px;
               padding: 6px 14px; color: {TEXT}; }}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:pressed {{ background: {BG}; }}
QPushButton:disabled {{ color: {TEXT_DIM}; border-color: {BORDER}; }}
QPushButton[accent="true"] {{ background: {ACCENT_DARK}; border-color: {ACCENT};
                              color: white; font-weight: bold; }}
QPushButton[accent="true"]:hover {{ background: {ACCENT}; }}
QPushButton[danger="true"] {{ background: transparent; border-color: {DANGER}; color: {DANGER}; }}

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {BG}; border: 1px solid {BORDER}; border-radius: 4px; padding: 4px 6px;
    selection-background-color: {ACCENT_DARK}; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{ background: {PANEL}; border: 1px solid {BORDER};
                               selection-background-color: {ACCENT_DARK}; }}

QSlider::groove:horizontal {{ height: 4px; background: {BORDER}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {ACCENT}; width: 12px; height: 12px;
                              margin: -5px 0; border-radius: 6px; }}
QSlider::groove:vertical {{ width: 4px; background: {BORDER}; border-radius: 2px; }}
QSlider::handle:vertical {{ background: {ACCENT}; height: 12px; margin: 0 -5px; border-radius: 6px; }}

QScrollBar:vertical {{ background: {BG}; width: 11px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 5px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {TEXT_DIM}; }}
QScrollBar:horizontal {{ background: {BG}; height: 11px; }}
QScrollBar::handle:horizontal {{ background: {BORDER}; border-radius: 5px; min-width: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}

QTreeWidget, QListWidget, QTableWidget {{ background: {PANEL}; border: 1px solid {BORDER};
    alternate-background-color: {PANEL_ALT}; }}
QTreeWidget::item, QListWidget::item {{ padding: 3px; border-radius: 3px; }}
QTreeWidget::item:selected, QListWidget::item:selected {{ background: {ACCENT_DARK}; color: white; }}
QHeaderView::section {{ background: {PANEL_ALT}; border: none; border-right: 1px solid {BORDER};
    padding: 4px 6px; }}

QGroupBox {{ border: 1px solid {BORDER}; border-radius: 6px; margin-top: 12px;
             font-weight: bold; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; color: {ACCENT}; }}

QProgressBar {{ background: {BG}; border: 1px solid {BORDER}; border-radius: 4px;
                text-align: center; height: 14px; }}
QProgressBar::chunk {{ background: {ACCENT_DARK}; border-radius: 3px; }}

QStatusBar {{ background: {PANEL}; border-top: 1px solid {BORDER}; color: {TEXT_DIM}; }}
QToolTip {{ background: {PANEL_ALT}; color: {TEXT}; border: 1px solid {ACCENT};
            padding: 4px 6px; }}
QSplitter::handle {{ background: {BORDER}; }}
QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid {BORDER};
    border-radius: 3px; background: {BG}; }}
QCheckBox::indicator:checked {{ background: {ACCENT_DARK}; border-color: {ACCENT}; }}
QLabel[dim="true"] {{ color: {TEXT_DIM}; }}
QLabel[h1="true"] {{ font-size: 15px; font-weight: bold; color: {TEXT}; }}
"""


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(BG))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
    pal.setColor(QPalette.ColorRole.Base, QColor(PANEL))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(PANEL_ALT))
    pal.setColor(QPalette.ColorRole.Text, QColor(TEXT))
    pal.setColor(QPalette.ColorRole.Button, QColor(PANEL_ALT))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT_DARK))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(PANEL_ALT))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(TEXT))
    pal.setColor(QPalette.ColorRole.Link, QColor(ACCENT))
    app.setPalette(pal)
    app.setStyleSheet(QSS)
