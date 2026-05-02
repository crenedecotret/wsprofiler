"""Dialog for chartread prompts and calibration instructions."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
)


class ChartReadDialog(QDialog):
    """Modal dialog to display chartread prompts and instructions."""

    def __init__(self, title: str, message: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(560)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.MinimumExpanding)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        # Message label
        self.message_label = QLabel(message)
        self.message_label.setWordWrap(True)
        self.message_label.setStyleSheet("font-size: 14px;")
        self.message_label.setMinimumWidth(520)
        self.message_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding)
        layout.addWidget(self.message_label)

        # Button area
        self.button_layout = QHBoxLayout()
        self.button_layout.addStretch()
        layout.addLayout(self.button_layout)

        self._buttons: dict[str, QPushButton] = {}
        self._result: str | None = None

    def add_button(self, key: str, label: str, default: bool = False, shortcut: str | None = None) -> None:
        """Add a button with the given key and label."""
        btn = QPushButton(label)
        btn.clicked.connect(lambda: self._on_button_clicked(key))
        btn.setAutoDefault(False)  # prevent arbitrary focus from making this the Enter-target
        if default:
            btn.setDefault(True)
            btn.setAutoDefault(True)
        if shortcut:
            # Route the shortcut to this button explicitly so keys like Space
            # don't fall through to whatever button currently has focus.
            sc = QShortcut(QKeySequence(shortcut), self)
            sc.activated.connect(btn.click)
        self.button_layout.addWidget(btn)
        self._buttons[key] = btn
    
    def _on_button_clicked(self, key: str) -> None:
        """Handle button click."""
        self._result = key
        self.accept()
    
    def get_result(self) -> str | None:
        """Get the result key from the clicked button."""
        return self._result


def show_calibration_dialog(message: str, parent=None) -> str | None:
    """Show calibration dialog and return the user action."""
    dialog = ChartReadDialog("Calibration Required", message, parent)
    dialog.add_button("enter", "Calibrate (Enter)", default=True)
    dialog.exec()
    return dialog.get_result()


def show_error_dialog(message: str, has_accept: bool = True, parent=None) -> str | None:
    """Show error reading dialog."""
    title = "Reading Error" if has_accept else "Retry Required"
    dialog = ChartReadDialog(title, message, parent)
    
    if has_accept:
        dialog.add_button("accept", "Accept (Enter)", default=True)
        dialog.add_button("retry", "Retry (Space)", shortcut="Space")
        dialog.add_button("giveup", "Give Up (Esc)", shortcut="Esc")
    else:
        dialog.add_button("retry", "Retry (Space)", default=True, shortcut="Space")
        dialog.add_button("giveup", "Give Up (Esc)", shortcut="Esc")
    
    dialog.exec()
    return dialog.get_result()


def show_confirm_dialog(message: str, parent=None) -> str | None:
    """Show confirmation dialog."""
    dialog = ChartReadDialog("Confirm", message, parent)
    dialog.add_button("yes", "Yes (y)", default=True)
    dialog.add_button("no", "No (n)")
    dialog.exec()
    return dialog.get_result()
