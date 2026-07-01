from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QCloseEvent, QGuiApplication
from PySide6.QtWidgets import (
    QMainWindow,
    QVBoxLayout,
    QWidget,
)

from .wizard import Wizard


class MainWindow(QMainWindow):
    def __init__(self, workspace: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.workspace = workspace

        self.setWindowTitle("wsprofiler — Argyll GUI")

        # The wizard owns its own session management and the
        # "Load Session…" button (in the step column at the bottom).
        self._wizard = Wizard(workspace=workspace)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(0)
        layout.addWidget(self._wizard)
        self.setCentralWidget(container)

        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            target_w = min(1800, max(800, avail.width() - 120))
            target_h = min(950, max(600, avail.height() - 120))
        else:
            target_w, target_h = 1800, 950
        self.resize(target_w, target_h)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._wizard.save_session_state()
        super().closeEvent(event)
