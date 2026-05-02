from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QStackedWidget,
    QWidget,
)

from .pages.generate_page import GeneratePage
from .pages.measurement_page import MeasurementPage
from .pages.profile_page import ProfilePage


class Wizard(QWidget):
    """Wizard layout with nav list + stacked pages."""

    def __init__(self, workspace: Path, parent=None) -> None:
        super().__init__(parent)
        self.workspace = workspace

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.steps = QListWidget()
        self.steps.setFixedWidth(180)
        self.steps.setAlternatingRowColors(True)
        layout.addWidget(self.steps)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, stretch=1)

        self._pages = {
            "Generate": GeneratePage(workspace=workspace),
            "Measure": MeasurementPage(workspace=workspace),
            "Profile": ProfilePage(workspace=workspace),
        }

        for title, page in self._pages.items():
            QListWidgetItem(title, self.steps)
            self.stack.addWidget(page)

        self.steps.currentRowChanged.connect(self._on_step_changed)
        self.steps.setCurrentRow(list(self._pages.keys()).index("Generate"))

        self._pending_ti2: Path | None = None
        self._pages["Generate"].chartGenerated.connect(self._on_chart_generated)
        self._pages["Measure"].measurementsComplete.connect(self._on_measurements_complete)

    def _on_chart_generated(self, ti2_path: Path) -> None:
        self._pending_ti2 = ti2_path
        
        # Show print instructions dialog
        tiff_folder = ti2_path.parent
        msg = QMessageBox(self)
        msg.setWindowTitle("Charts Ready for Printing")
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setText(
            f"The chart files have been generated in:\n\n{tiff_folder}\n\n"
            "Please print the TIFF files at their original size with "
            "color management DISABLED in your printer driver.\n\n"
            "Important:\n"
            "• Write down your printer settings (paper type, quality, etc.)\n"
            "• The generated ICC profile will be unique to these settings\n"
            "• Allow prints to dry for at least 1 hour before measuring"
        )
        msg.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg.exec()
        
        # Switch to measurement page after user clicks OK
        measure_idx = list(self._pages.keys()).index("Measure")
        self.steps.setCurrentRow(measure_idx)

    def _on_measurements_complete(self, ti3_path: Path) -> None:
        """Show dialog when all measurements are done, offer to go to Profile page."""
        msg = QMessageBox(self)
        msg.setWindowTitle("Measurements Complete")
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setText(
            "All chart measurements are complete!\n\n"
            f"Measurement data saved to:\n{ti3_path}\n\n"
            "You are now ready to create the ICC profile."
        )
        msg.setStandardButtons(
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Ok
        )
        msg.button(QMessageBox.StandardButton.Cancel).setText("Return to measurements")
        msg.button(QMessageBox.StandardButton.Ok).setText("Create Profile")
        
        result = msg.exec()
        if result == QMessageBox.StandardButton.Ok:
            # Switch to Profile page
            profile_idx = list(self._pages.keys()).index("Profile")
            self.steps.setCurrentRow(profile_idx)

    def _on_step_changed(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        page = self.stack.widget(index)
        if page is self._pages["Measure"] and self._pending_ti2 is not None:
            if self._pending_ti2.exists():
                page.load_ti2(self._pending_ti2)
            self._pending_ti2 = None
        if page is self._pages["Profile"]:
            ti3_path = self._pages["Measure"].get_current_ti3_path()
            if ti3_path:
                page.set_ti3_path(ti3_path)
