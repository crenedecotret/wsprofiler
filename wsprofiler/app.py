from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from .ui.main_window import MainWindow
from .ui.theme import apply_theme


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("wsprofiler")
    # Leave organizationName unset so QStandardPaths.AppDataLocation
    # resolves to <data>/wsprofiler rather than <data>/wsprofiler/wsprofiler.
    # The product is "wsprofiler" — there is no umbrella org.
    app.setOrganizationName("")

    apply_theme(app)

    root = Path.cwd()
    window = MainWindow(workspace=root)
    window.show()

    return app.exec()
