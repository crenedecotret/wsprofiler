from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)


class InstrumentPanel(QFrame):
    calibrateRequested = Signal()
    modeChanged = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)

        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        self.title = QLabel("Measurement")
        self.title.setStyleSheet("font-size: 18px; font-weight: 600;")
        layout.addWidget(self.title)

        self.device_status = QLabel("Device status: Not connected")
        layout.addWidget(self.device_status)

        layout.addWidget(QLabel("Mode"))
        mode_group = QButtonGroup(self)
        self.radio_strip = QRadioButton("Single scan (strip)")
        self.radio_spot = QRadioButton("Spot measurements")
        self.radio_strip.setChecked(True)
        mode_group.addButton(self.radio_strip)
        mode_group.addButton(self.radio_spot)
        layout.addWidget(self.radio_strip)
        layout.addWidget(self.radio_spot)

        self.calibrate_button = QPushButton("Calibrate")
        layout.addWidget(self.calibrate_button)

        layout.addStretch()

        self.calibrate_button.clicked.connect(self.calibrateRequested)
        self.radio_strip.toggled.connect(
            lambda checked: checked and self.modeChanged.emit("strip")
        )
        self.radio_spot.toggled.connect(lambda checked: checked and self.modeChanged.emit("spot"))

    def set_status(self, text: str) -> None:
        self.device_status.setText(text)
