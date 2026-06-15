"""Horizontal step navigation bar with numbered circles."""
from __future__ import annotations

from PySide6.QtCore import Property, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QWidget

from .theme import Colors


class StepCircle(QWidget):
    """A single step indicator: numbered circle + label below."""

    clicked = Signal()

    def __init__(self, number: int, label: str, parent=None) -> None:
        super().__init__(parent)
        self._number = number
        self._label_text = label
        self._state = "inactive"  # "inactive" | "active" | "completed"
        self._circle_size = 28

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # Let the widget be as wide as its label needs
        self.setMinimumWidth(self._calc_min_width())

    def _calc_min_width(self) -> int:
        """Calculate minimum width to fit the label text."""
        fm = QFontMetrics(QFont("Segoe UI", 10))
        text_width = fm.horizontalAdvance(self._label_text)
        # Ensure circle is always visible (circle + some padding)
        return max(text_width + 16, self._circle_size + 16)

    def sizeHint(self):
        from PySide6.QtCore import QSize
        fm = QFontMetrics(QFont("Segoe UI", 10))
        w = max(fm.horizontalAdvance(self._label_text) + 16, self._circle_size + 16)
        h = self._circle_size + 22  # circle + gap + label
        return QSize(w, h)

    def get_state(self) -> str:
        return self._state

    def set_state(self, state: str) -> None:
        self._state = state
        self.update()

    state = Property(str, get_state, set_state)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        cx = self.width() / 2
        cy = self._circle_size / 2 + 2
        radius = self._circle_size / 2

        # Circle colors by state
        if self._state == "active":
            bg = QColor(Colors.STEP_ACTIVE)
            text_color = QColor(Colors.TEXT_ON_ACCENT)
            border_color = QColor(Colors.STEP_ACTIVE)
        elif self._state == "completed":
            bg = QColor(Colors.STEP_COMPLETED)
            text_color = QColor(Colors.TEXT_ON_ACCENT)
            border_color = QColor(Colors.STEP_COMPLETED)
        else:
            bg = QColor(Colors.BG_INPUT)
            text_color = QColor(Colors.TEXT_MUTED)
            border_color = QColor(Colors.BORDER)

        # Draw circle
        painter.setPen(QPen(border_color, 2))
        painter.setBrush(bg)
        painter.drawEllipse(int(cx - radius), int(cy - radius), int(radius * 2), int(radius * 2))

        # Draw number or checkmark — centered within the circle
        font = QFont("Segoe UI", 11, QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(text_color)
        if self._state == "completed":
            text = "\u2713"
        else:
            text = str(self._number)
        circle_rect = QRectF(cx - radius, cy - radius, radius * 2, radius * 2)
        painter.drawText(circle_rect, Qt.AlignmentFlag.AlignCenter, text)

        # Draw label below circle
        label_font = QFont("Segoe UI", 10)
        painter.setFont(label_font)
        if self._state == "active":
            painter.setPen(QColor(Colors.STEP_ACTIVE))
        elif self._state == "completed":
            painter.setPen(QColor(Colors.STEP_COMPLETED))
        else:
            painter.setPen(QColor(Colors.TEXT_MUTED))
        painter.drawText(
            self.rect().adjusted(0, self._circle_size + 4, 0, 0),
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter,
            self._label_text,
        )

        painter.end()

    def mousePressEvent(self, event) -> None:
        self.clicked.emit()


class StepBar(QFrame):
    """Horizontal bar showing numbered steps with connecting lines.

    Steps are 1-indexed in the API. The active step is highlighted.
    Steps before the active step show as completed (green checkmark).
    """

    stepClicked = Signal(int)  # emits 1-indexed step number

    def __init__(self, steps: list[str], parent=None) -> None:
        super().__init__(parent)
        self._steps = steps
        self._active = 1
        self._completed: set[int] = set()

        self.setObjectName("stepBar")
        self.setMinimumHeight(68)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 6, 20, 6)
        layout.setSpacing(0)

        self._circles: list[StepCircle] = []
        self._connectors: list[QFrame] = []

        for i, label in enumerate(steps):
            circle = StepCircle(i + 1, label)
            circle.clicked.connect(lambda idx=i + 1: self.stepClicked.emit(idx))
            self._circles.append(circle)
            layout.addWidget(circle)

            if i < len(steps) - 1:
                connector = QFrame()
                connector.setFixedHeight(2)
                connector.setMinimumWidth(16)
                connector.setStyleSheet(f"background-color: {Colors.BORDER}; border-radius: 1px;")
                self._connectors.append(connector)
                layout.addWidget(connector)

        self._update_visuals()

    @property
    def active_step(self) -> int:
        return self._active

    def set_active(self, step: int) -> None:
        """Set the currently active step (1-indexed)."""
        self._active = step
        self._update_visuals()

    def mark_completed(self, step: int) -> None:
        """Mark a step as completed."""
        self._completed.add(step)
        self._update_visuals()

    def clear_completed(self) -> None:
        """Clear all completed markers."""
        self._completed.clear()
        self._update_visuals()

    def _update_visuals(self) -> None:
        for i, circle in enumerate(self._circles):
            step_num = i + 1
            if step_num == self._active:
                circle.set_state("active")
            elif step_num in self._completed:
                circle.set_state("completed")
            else:
                circle.set_state("inactive")

        for i, connector in enumerate(self._connectors):
            step_num = i + 1
            if step_num < self._active:
                connector.setStyleSheet(
                    f"background-color: {Colors.STEP_COMPLETED}; border-radius: 1px;"
                )
            else:
                connector.setStyleSheet(
                    f"background-color: {Colors.BORDER}; border-radius: 1px;"
                )
