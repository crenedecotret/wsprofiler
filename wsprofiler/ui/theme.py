"""Central theme system for wsprofiler dark UI."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


def _base_path() -> Path:
    """Return the base directory for bundled resources.

    When frozen by PyInstaller, resources are extracted to sys._MEIPASS.
    Otherwise, resolve relative to this file.
    """
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).parent


class Colors:
    """Dark theme color palette — neutral gray accent."""

    # Backgrounds
    BG_PRIMARY = "#1b1f2b"
    BG_SECONDARY = "#161a24"
    BG_SURFACE = "#222838"
    BG_INPUT = "#141820"
    BG_HOVER = "#2a3042"
    BG_SELECTED = "#2e364a"

    # Accent — neutral gray (buttons, inputs)
    ACCENT = "#5a6272"
    ACCENT_HOVER = "#6a7282"
    ACCENT_PRESSED = "#4a5262"
    ACCENT_SUBTLE = "rgba(90, 98, 114, 0.2)"

    # Step bar — teal (top navigation only)
    STEP_ACTIVE = "#4A9B8E"
    STEP_ACTIVE_HOVER = "#5BB5A7"
    STEP_COMPLETED = "#3D8579"

    # Semantic
    SUCCESS = "#5BA47E"
    SUCCESS_SUBTLE = "rgba(91, 164, 126, 0.15)"
    WARNING = "#D4A76A"
    WARNING_SUBTLE = "rgba(212, 167, 106, 0.15)"
    ERROR = "#C75050"
    ERROR_SUBTLE = "rgba(199, 80, 80, 0.15)"

    # Text
    TEXT_PRIMARY = "#e0e0e8"
    TEXT_SECONDARY = "#8a8ea0"
    TEXT_MUTED = "#555a6e"
    TEXT_ON_ACCENT = "#ffffff"

    # Borders
    BORDER = "#2a2f3e"
    BORDER_FOCUS = "#5a6272"
    BORDER_SUBTLE = "#1e222e"

    # Chart view
    CHART_BG = "#1b1f2b"
    CHART_HEADER = "#8a8ea0"

    # Console / monospace
    CONSOLE_BG = "#111418"
    CONSOLE_TEXT = "#b0b8c8"


def apply_theme(app: QApplication) -> None:
    """Apply the dark theme to the application.

    Loads dark.qss and applies a base QPalette for widgets
    not covered by the stylesheet.
    """
    base = _base_path()
    qss_path = base / "styles" / "dark.qss"
    if qss_path.exists():
        qss = qss_path.read_text()
        # Inject absolute paths for icons
        assets_dir = base / "assets"
        checkmark = assets_dir / "checkmark.png"
        dropdown = assets_dir / "dropdown_arrow.png"
        spin_up = assets_dir / "spinbox_up.png"
        spin_down = assets_dir / "spinbox_down.png"
        qss = qss.replace("__CHECKMARK_PATH__", checkmark.as_posix())
        qss = qss.replace("__DROPDOWN_PATH__", dropdown.as_posix())
        qss = qss.replace("__SPIN_UP_PATH__", spin_up.as_posix())
        qss = qss.replace("__SPIN_DOWN_PATH__", spin_down.as_posix())
        app.setStyleSheet(qss)

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(Colors.BG_PRIMARY))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(Colors.TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.Base, QColor(Colors.BG_INPUT))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(Colors.BG_SECONDARY))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(Colors.BG_SURFACE))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(Colors.TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.Text, QColor(Colors.TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.Button, QColor(Colors.BG_SURFACE))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(Colors.TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.BrightText, QColor(Colors.ACCENT))
    palette.setColor(QPalette.ColorRole.Link, QColor(Colors.ACCENT))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(Colors.ACCENT))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(Colors.TEXT_ON_ACCENT))

    # Disabled group
    palette.setColor(
        QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText,
        QColor(Colors.TEXT_MUTED),
    )
    palette.setColor(
        QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text,
        QColor(Colors.TEXT_MUTED),
    )
    palette.setColor(
        QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText,
        QColor(Colors.TEXT_MUTED),
    )

    app.setPalette(palette)
