"""Tests for SessionManagerDialog."""
from __future__ import annotations

import json
import os
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

# Force offscreen Qt platform BEFORE any PySide6 import.
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from wsprofiler.ui.session_manager_dialog import SessionManagerDialog


def _ensure_app():
    return QApplication.instance() or QApplication([])


def _make_wsp(
    tmp_path: Path,
    name: str,
    target_name: str = "mytarget",
    generated_at: str | None = None,
    files: dict[str, str] | None = None,
    optimisation_count: int = 0,
    profile_configs: list[dict] | None = None,
    generate_config: dict | None = None,
    mtime: float | None = None,
) -> Path:
    """Create a minimal .wsp zip with a manifest.json inside."""
    wsp = tmp_path / f"{name}.wsp"
    manifest = {
        "version": 1,
        "wizard_type": "simple",
        "target_name": target_name,
        "workspace": str(tmp_path),
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "files": files or {},
        "generate_config": generate_config or {},
        "profile_configs": profile_configs or [],
        "optimisation_count": optimisation_count,
    }
    with zipfile.ZipFile(wsp, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
    if mtime is not None:
        os.utime(wsp, (mtime, mtime))
    return wsp


def _show(dialog: SessionManagerDialog) -> None:
    """Make the dialog visible so isVisible() works in offscreen tests."""
    dialog.setVisible(True)


# ------------------------------------------------------------------ scan

def test_empty_state(tmp_path: Path):
    """When no sessions exist the empty label is shown."""
    _ensure_app()
    dialog = SessionManagerDialog(tmp_path)
    _show(dialog)
    assert dialog._empty_label.isVisible()
    assert not dialog._table.isVisible()
    assert dialog._table.rowCount() == 0


def test_populates_table(tmp_path: Path):
    """The table lists .wsp files sorted by newest first."""
    _ensure_app()
    older = _make_wsp(
        tmp_path,
        "older",
        generated_at="2026-06-01T10:00:00+00:00",
        files={"ti2": "x.ti2"},
        mtime=1_000_000,
    )
    newer = _make_wsp(
        tmp_path,
        "newer",
        generated_at="2026-06-02T12:00:00+00:00",
        files={"ti2": "x.ti2", "ti3": "x.ti3"},
        mtime=2_000_000,
    )

    dialog = SessionManagerDialog(tmp_path)
    _show(dialog)
    assert not dialog._empty_label.isVisible()
    assert dialog._table.isVisible()
    assert dialog._table.rowCount() == 2

    # Newest first (name is in column 3 now)
    assert dialog._table.item(0, 3).text() == "newer"
    assert dialog._table.item(1, 3).text() == "older"


def test_step_inference(tmp_path: Path):
    """Step labels are derived from file roles and optimisation_count."""
    _ensure_app()
    expected_map = {
        "gen": "Read chart",
        "read": "Read chart",
        "prof": "Generate profile",
        "complete": "Complete profile",
        "opt": "Optimised profile",
        "old_read": "Read chart",
    }
    _make_wsp(tmp_path, "gen", files={}, mtime=1_000_000)
    _make_wsp(tmp_path, "read", files={"ti2": "x.ti2"}, mtime=900_000)
    _make_wsp(
        tmp_path,
        "prof",
        files={"ti2": "x.ti2", "ti3": "x.ti3"},
        mtime=800_000,
    )
    _make_wsp(
        tmp_path,
        "complete",
        files={"ti2": "x.ti2", "ti3": "x.ti3", "icc": "x.icc"},
        mtime=700_000,
    )
    _make_wsp(
        tmp_path,
        "opt",
        files={"ti2": "x.ti2", "ti3": "x.ti3", "icc": "x.icc"},
        optimisation_count=1,
        mtime=600_000,
    )
    _make_wsp(
        tmp_path,
        "old_read",
        files={"chart1_ti2": "old.ti2"},
        mtime=500_000,
    )

    dialog = SessionManagerDialog(tmp_path)
    for row in range(dialog._table.rowCount()):
        name = dialog._table.item(row, 3).text()
        assert dialog._table.item(row, 1).text() == expected_map[name]


def test_profile_name(tmp_path: Path):
    """Profile name prefers generate_config.target_path, then target_name, then filename stem."""
    _ensure_app()
    # Has generate_config.target_path → shows stem of that path
    _make_wsp(
        tmp_path, "fileC",
        target_name="ArgyllStem",
        generate_config={"target_path": "/home/user/MyPrinter.icc"},
        mtime=2_000_000,
    )
    # Has target_name but no target_path → shows target_name
    _make_wsp(tmp_path, "fileA", target_name="MyProfile", mtime=1_000_000)
    # Neither target_path nor target_name → falls back to filename stem
    _make_wsp(tmp_path, "fileB", target_name="", mtime=900_000)

    dialog = SessionManagerDialog(tmp_path)
    assert dialog._table.item(0, 0).text() == "MyPrinter"
    assert dialog._table.item(1, 0).text() == "MyProfile"
    assert dialog._table.item(2, 0).text() == "fileB"


def test_timestamp_formatting(tmp_path: Path):
    """ISO timestamps are rendered in a human-readable local format."""
    _ensure_app()
    _make_wsp(tmp_path, "ts", generated_at="2026-06-28T15:30:00+00:00")

    dialog = SessionManagerDialog(tmp_path)
    cell_text = dialog._table.item(0, 2).text()
    assert "2026-06-28" in cell_text
    assert ":30" in cell_text


# ------------------------------------------------------------------ selection

def test_selection_enables_buttons(tmp_path: Path):
    """Load and Delete buttons are disabled until a row is selected."""
    _ensure_app()
    _make_wsp(tmp_path, "sess")
    dialog = SessionManagerDialog(tmp_path)

    assert not dialog._load_btn.isEnabled()
    assert not dialog._delete_btn.isEnabled()

    dialog._table.selectRow(0)
    assert dialog._load_btn.isEnabled()
    assert dialog._delete_btn.isEnabled()


# ------------------------------------------------------------------ load

def test_load_returns_selected_path(tmp_path: Path):
    """Selecting a row and accepting returns the session path."""
    _ensure_app()
    wsp = _make_wsp(tmp_path, "sess")
    dialog = SessionManagerDialog(tmp_path)
    dialog._table.selectRow(0)
    dialog._on_load_clicked()
    assert dialog.result() == SessionManagerDialog.Accepted
    assert dialog.selected_path() == wsp


def test_cancel_returns_none(tmp_path: Path):
    """Cancelling the dialog yields no selected path."""
    _ensure_app()
    _make_wsp(tmp_path, "sess")
    dialog = SessionManagerDialog(tmp_path)
    dialog._on_cancel_clicked()
    assert dialog.result() == SessionManagerDialog.Rejected
    assert dialog.selected_path() is None


# ------------------------------------------------------------------ delete

def test_delete_removes_file_and_refreshes(tmp_path: Path, monkeypatch):
    """Deleting a session removes the .wsp and updates the table."""
    _ensure_app()
    wsp = _make_wsp(tmp_path, "sess")
    dialog = SessionManagerDialog(tmp_path)
    _show(dialog)
    assert dialog._table.rowCount() == 1

    dialog._table.selectRow(0)

    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **kw: QMessageBox.Yes),
    )
    dialog._on_delete_clicked()

    assert not wsp.exists()
    assert dialog._table.rowCount() == 0
    assert dialog._empty_label.isVisible()
    assert not dialog._load_btn.isEnabled()
    assert not dialog._delete_btn.isEnabled()
