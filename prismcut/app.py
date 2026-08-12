"""Application entry point."""
from __future__ import annotations

import sys


def main(app=None) -> int:
    """app is normally None (the real entry point below always calls it that
    way) - the parameter exists so tests can pass an already-constructed
    QApplication instead of hitting Qt's "only one QApplication per process"
    restriction when this runs inside a test suite that already has one."""
    from PySide6.QtWidgets import QApplication

    from . import APP_NAME, ORG_NAME
    from .core.settings import Settings
    from .ui import theme
    from .ui.main_window import MainWindow

    if app is None:
        app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    # Without settings, apply_theme() has no saved choice to read and always
    # falls back to following the OS light/dark preference - the exact
    # "starts in light mode even with dark mode saved" bug.
    theme.apply_theme(app, Settings())
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
