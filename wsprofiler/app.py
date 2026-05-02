from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from .ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("wsprofiler")
    app.setOrganizationName("wsprofiler")

    root = Path.cwd()
    window = MainWindow(workspace=root)
    window.show()

    return app.exec()
