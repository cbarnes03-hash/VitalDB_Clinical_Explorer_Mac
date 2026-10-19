"""Application entry point."""
from __future__ import annotations

import logging
import sys


def _setup_logging() -> None:
    from . import config
    try:
        logging.basicConfig(filename=str(config.data_dir() / "app.log"), level=logging.INFO,
                            format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    except OSError:
        logging.basicConfig(level=logging.INFO)


def _force_light(app) -> None:
    """The design is light-only: stop macOS/Windows dark mode from turning half the widgets dark."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QPalette
    try:
        app.styleHints().setColorScheme(Qt.ColorScheme.Light)       # Qt 6.8+
    except Exception:
        pass
    pal = QPalette()
    for role, col in ((QPalette.Window, "#F3F6F9"), (QPalette.WindowText, "#1B2A38"), (QPalette.Base, "#FFFFFF"),
                      (QPalette.AlternateBase, "#F8FAFC"), (QPalette.Text, "#1B2A38"), (QPalette.Button, "#FFFFFF"),
                      (QPalette.ButtonText, "#1B2A38"), (QPalette.Highlight, "#E4F3F6"),
                      (QPalette.HighlightedText, "#1B2A38"), (QPalette.ToolTipBase, "#0E2536"),
                      (QPalette.ToolTipText, "#FFFFFF"), (QPalette.PlaceholderText, "#8795A3")):
        pal.setColor(role, QColor(col))
    app.setPalette(pal)


def create_window():
    """Build the QApplication (if needed) and main window. Used by tests too."""
    import matplotlib
    matplotlib.use("QtAgg")
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication

    from .gui.main_window import MainWindow
    from .gui.style import QT_FONT, STYLESHEET, apply_matplotlib_style

    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    _force_light(app)
    app.setStyleSheet(STYLESHEET)
    app.setFont(QFont(QT_FONT, 10 if sys.platform.startswith("win") else 13))
    apply_matplotlib_style()
    return app, MainWindow()


def main() -> int:
    _setup_logging()
    log = logging.getLogger("vitaldb_explorer")
    try:
        app, win = create_window()
    except Exception as exc:  # pragma: no cover - startup problem
        log.exception("Startup failure")
        _fatal(f"The application could not start:\n\n{exc}\n\nPlease run install_windows.bat again.")
        return 1
    win.show()
    return app.exec()


def _fatal(message: str) -> None:
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, message, "VitalDB Clinical Explorer", 0x10)
    except Exception:
        print(message, file=sys.stderr)
