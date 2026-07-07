"""Session Manager dialog for listing, loading, and deleting saved sessions."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..session import read_manifest


class SessionManagerDialog(QDialog):
    """Modal dialog listing saved .wsp sessions with metadata."""

    def __init__(self, sessions_dir: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Session Manager")
        self.setModal(True)
        self.setMinimumWidth(720)
        self.setMinimumHeight(400)
        self.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.MinimumExpanding
        )

        self._sessions_dir = sessions_dir
        self._selected_path: Path | None = None
        self._new_session: bool = False

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        # Title
        title = QLabel("Saved Sessions")
        title.setStyleSheet("font-size: 20px; font-weight: 600;")
        layout.addWidget(title)

        # Empty-state label (shown when no sessions)
        self._empty_label = QLabel("No saved sessions found.")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet("font-size: 14px; color: #8a8ea0;")
        self._empty_label.setVisible(False)
        layout.addWidget(self._empty_label, stretch=1)

        # Table
        self._table = QTableWidget()
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels(
            ["Profile Name", "Step", "Saved At", "Filename"]
        )
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.verticalHeader().hide()
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.horizontalHeader().setHighlightSections(False)
        self._table.setColumnWidth(0, 180)
        self._table.setColumnWidth(1, 140)
        self._table.setColumnWidth(2, 160)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        self._table.doubleClicked.connect(self._on_double_clicked)
        layout.addWidget(self._table, stretch=1)

        # Buttons
        button_layout = QHBoxLayout()

        new_btn = QPushButton("New Session")
        new_btn.clicked.connect(self._on_new_clicked)
        button_layout.addWidget(new_btn)

        button_layout.addStretch()

        self._load_btn = QPushButton("Load")
        self._load_btn.setDefault(True)
        self._load_btn.setEnabled(False)
        self._load_btn.clicked.connect(self._on_load_clicked)
        button_layout.addWidget(self._load_btn)

        self._delete_btn = QPushButton("Delete")
        self._delete_btn.setEnabled(False)
        self._delete_btn.clicked.connect(self._on_delete_clicked)
        button_layout.addWidget(self._delete_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self._on_cancel_clicked)
        button_layout.addWidget(cancel_btn)

        layout.addLayout(button_layout)

        self._scan_sessions()

    def selected_path(self) -> Path | None:
        """Return the path chosen by the user, or None if cancelled."""
        return self._selected_path

    def is_new_session(self) -> bool:
        """Return True if the user requested a fresh new session."""
        return self._new_session

    def _scan_sessions(self) -> None:
        """Scan the sessions directory and populate the table."""
        self._table.setRowCount(0)
        if not self._sessions_dir.exists():
            self._empty_label.setVisible(True)
            self._table.setVisible(False)
            return

        wsp_files = sorted(
            self._sessions_dir.glob("*.wsp"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        if not wsp_files:
            self._empty_label.setVisible(True)
            self._table.setVisible(False)
            return

        self._empty_label.setVisible(False)
        self._table.setVisible(True)

        for path in wsp_files:
            manifest = read_manifest(path)
            if not manifest:
                continue
            row = self._table.rowCount()
            self._table.insertRow(row)

            # Column 0: Profile Name
            profile_name = self._get_profile_name(manifest, path)
            item = QTableWidgetItem(profile_name)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 0, item)

            # Column 1: Step
            step_label = self._get_step_label(manifest)
            self._table.setItem(row, 1, self._read_only_item(step_label))

            # Column 2: Saved At
            generated_at = manifest.get("generated_at", "")
            saved_str = self._format_timestamp(generated_at)
            self._table.setItem(row, 2, self._read_only_item(saved_str))

            # Column 3: Filename
            item = QTableWidgetItem(path.stem)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 3, item)

    @staticmethod
    def _read_only_item(text: str) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        return item

    @staticmethod
    def _format_timestamp(iso_str: str) -> str:
        if not iso_str:
            return "—"
        try:
            # Parse ISO-8601 with or without timezone info
            if iso_str.endswith("Z"):
                iso_str = iso_str[:-1] + "+00:00"
            dt = datetime.fromisoformat(iso_str)
            # Convert to local time for display
            if dt.tzinfo is not None:
                dt = dt.astimezone()
            return dt.strftime("%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            return "—"

    @staticmethod
    def _get_step_label(manifest: dict[str, Any]) -> str:
        files = manifest.get("files", {})
        optimisation_count = manifest.get("optimisation_count", 0)

        has_ti2 = "ti2" in files or "chart1_ti2" in files
        has_ti3 = "ti3" in files or "chart1_ti3" in files
        has_icc = "icc" in files or "chart1_icc" in files

        if has_ti2 and not has_ti3:
            return "Read chart"
        if has_ti3 and not has_icc:
            return "Generate profile"
        if has_icc and optimisation_count == 0:
            return "Complete profile"
        if has_icc and optimisation_count > 0:
            last_pass_icc = f"pass{optimisation_count}_icc"
            if last_pass_icc in files:
                return "Optimised profile"
            return "Optimising (in progress)"
        return "Read chart"

    @staticmethod
    def _get_profile_name(manifest: dict[str, Any], path: Path) -> str:
        target_path = manifest.get("generate_config", {}).get("target_path")
        if target_path:
            return Path(target_path).stem
        target_name = manifest.get("target_name")
        if target_name:
            return target_name
        return path.stem

    def _on_selection_changed(self) -> None:
        selected = self._table.selectionModel().hasSelection()
        self._load_btn.setEnabled(selected)
        self._delete_btn.setEnabled(selected)

    def _on_double_clicked(self) -> None:
        self._on_load_clicked()

    def _on_load_clicked(self) -> None:
        row = self._table.currentRow()
        if row < 0:
            return
        item = self._table.item(row, 3)
        path_str = item.data(Qt.ItemDataRole.UserRole)
        if path_str:
            self._selected_path = Path(path_str)
            self.accept()

    def _on_delete_clicked(self) -> None:
        row = self._table.currentRow()
        if row < 0:
            return
        item = self._table.item(row, 3)
        path_str = item.data(Qt.ItemDataRole.UserRole)
        if not path_str:
            return
        path = Path(path_str)

        reply = QMessageBox.question(
            self,
            "Delete Session",
            f"Are you sure you want to delete <b>{path.stem}</b>?<br>"
            "This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            path.unlink()
        except OSError as exc:
            QMessageBox.critical(self, "Delete Failed", str(exc))
            return

        self._scan_sessions()
        self._load_btn.setEnabled(False)
        self._delete_btn.setEnabled(False)

    def _on_new_clicked(self) -> None:
        self._new_session = True
        self._selected_path = None
        self.accept()

    def _on_cancel_clicked(self) -> None:
        self.reject()
