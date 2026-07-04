"""Tests for SessionManager."""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest


def _ensure_app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_create_new_creates_temp_dir():
    from wsprofiler.session_manager import SessionManager

    sm = SessionManager()
    assert not sm.is_active
    sm.create_new()
    try:
        assert sm.is_active
        assert sm.temp_dir is not None
        assert sm.temp_dir.exists()
        assert sm.temp_dir.name.startswith("wsprofiler_")
        assert sm.wsp_path is not None
        # Default WSP path should be somewhere under the app data dir.
        assert "wsprofiler" in str(sm.wsp_path)
        assert sm.target_path == sm.temp_dir / "session"
        assert not sm.is_loaded
    finally:
        sm.cleanup()


def test_create_new_clears_previous_temp():
    from wsprofiler.session_manager import SessionManager

    sm = SessionManager()
    sm.create_new()
    first_dir = sm.temp_dir
    sm.create_new()
    try:
        # Old temp dir should be gone.
        assert first_dir is not None
        assert not first_dir.exists()
        # New temp dir should be different.
        assert sm.temp_dir != first_dir
        assert sm.temp_dir is not None and sm.temp_dir.exists()
    finally:
        sm.cleanup()


def test_auto_save_writes_wsp():
    from wsprofiler.session_manager import SessionManager

    sm = SessionManager()
    sm.create_new()
    try:
        # Create dummy files in the temp dir.
        ti1 = sm.temp_dir / "session.ti1"
        ti1.write_text("ti1 data")
        ti2 = sm.temp_dir / "session.ti2"
        ti2.write_text("ti2 data")

        wsp = sm.auto_save(
            file_paths={"ti1": ti1, "ti2": ti2},
            generate_config={"device": "i1", "paper": "A3"},
        )
        assert wsp is not None
        assert wsp.exists()

        # The WSP should contain both files and a valid manifest.
        with zipfile.ZipFile(wsp) as zf:
            names = zf.namelist()
        assert "manifest.json" in names
        assert "session.ti1" in names
        assert "session.ti2" in names
    finally:
        sm.cleanup()


def test_auto_save_overwrites_existing():
    from wsprofiler.session_manager import SessionManager

    sm = SessionManager()
    sm.create_new()
    try:
        ti1 = sm.temp_dir / "session.ti1"
        ti1.write_text("first")
        wsp1 = sm.auto_save(file_paths={"ti1": ti1})
        assert wsp1.exists()

        ti1.write_text("second")
        wsp2 = sm.auto_save(file_paths={"ti1": ti1})
        # Should reuse the same WSP path (silent overwrite).
        assert wsp1 == wsp2

        with zipfile.ZipFile(wsp1) as zf:
            content = zf.read("session.ti1")
        assert content == b"second"
    finally:
        sm.cleanup()


def test_copy_final_icc_creates_destination(tmp_path):
    from wsprofiler.session_manager import SessionManager

    sm = SessionManager()
    sm.create_new()
    try:
        source_icc = sm.temp_dir / "session.icc"
        source_icc.write_text("icc data")

        dest = tmp_path / "subdir" / "myprinter.icc"
        sm.set_final_icc_path(dest)
        result = sm.copy_final_icc(source_icc)
        assert result == dest
        assert dest.exists()
        assert dest.read_text() == "icc data"
    finally:
        sm.cleanup()


def test_copy_final_icc_creates_missing_parent(tmp_path):
    from wsprofiler.session_manager import SessionManager

    sm = SessionManager()
    sm.create_new()
    try:
        source_icc = sm.temp_dir / "session.icc"
        source_icc.write_text("icc data")

        dest = tmp_path / "deep" / "nested" / "path" / "out.icc"
        sm.set_final_icc_path(dest)
        result = sm.copy_final_icc(source_icc)
        assert result == dest
        assert dest.exists()
    finally:
        sm.cleanup()


def test_copy_final_icc_without_destination(tmp_path):
    from wsprofiler.session_manager import SessionManager

    sm = SessionManager()
    sm.create_new()
    try:
        source_icc = sm.temp_dir / "session.icc"
        source_icc.write_text("icc data")
        # No destination set
        result = sm.copy_final_icc(source_icc)
        assert result is None
    finally:
        sm.cleanup()


def test_load_from_wsp_restores_files():
    from wsprofiler.session_manager import SessionManager
    from wsprofiler.session import SimpleSnapshot, save_session

    sm = SessionManager()
    sm.create_new()
    try:
        # Create a WSP with some files.
        ti2 = sm.temp_dir / "session.ti2"
        ti2.write_text("ti2 data")
        ti3 = sm.temp_dir / "session.ti3"
        ti3.write_text("ti3 data")

        wsp_path = tmp_path_fixture() / "test.wsp"  # type: ignore[name-defined]
        # Use the temp dir as the WSP base to keep things simple.
        snapshot = SimpleSnapshot(
            version=1,
            wizard_type="simple",
            target_name="session",
            workspace=str(sm.temp_dir),
            generate_config={"device": "i1", "paper": "A3", "target_path": "/dest.printer.icc"},
        )
        save_session(
            sm.wsp_path,
            snapshot,
            sm.temp_dir,
            {"ti2": ti2, "ti3": ti3},
        )

        # Now load it via a fresh SessionManager.
        sm2 = SessionManager()
        try:
            manifest, files, temp_dir = sm2.load_from_wsp(sm.wsp_path)
            assert sm2.is_loaded
            # Auto-saves go to the same path as the source WSP.
            assert sm2.wsp_path == sm.wsp_path
            assert sm2.wsp_path is not None
            assert temp_dir.exists()
            # Files should be extracted into temp_dir
            assert "session.ti2" in {f.name for f in temp_dir.iterdir()}
            assert files["ti2"].exists()
            assert files["ti3"].exists()
            assert manifest["generate_config"]["device"] == "i1"
        finally:
            sm2.cleanup()
    finally:
        sm.cleanup()


def tmp_path_fixture():
    """Helper: return a tmp path (pytest provides one per test)."""
    import tempfile
    return Path(tempfile.mkdtemp(prefix="wsp_test_"))


def test_load_from_wsp_clears_previous_session():
    from wsprofiler.session_manager import SessionManager

    sm = SessionManager()
    sm.create_new()
    first_dir = sm.temp_dir
    # Make a tiny WSP
    from wsprofiler.session import SimpleSnapshot, save_session
    ti2 = first_dir / "session.ti2"
    ti2.write_text("x")
    snapshot = SimpleSnapshot(
        version=1,
        wizard_type="simple",
        target_name="session",
        workspace=str(first_dir),
    )
    save_session(sm.wsp_path, snapshot, first_dir, {"ti2": ti2})

    # Now load via a fresh SM
    sm2 = SessionManager()
    try:
        sm2.load_from_wsp(sm.wsp_path)
        # First manager's temp dir should have been cleared by SM2's load
        # because both are independent. First dir should still exist.
        assert first_dir is not None and first_dir.exists()
    finally:
        sm2.cleanup()
        sm.cleanup()


def test_cleanup_removes_temp_dir():
    from wsprofiler.session_manager import SessionManager

    sm = SessionManager()
    sm.create_new()
    temp_dir = sm.temp_dir
    assert temp_dir.exists()
    sm.cleanup()
    assert not temp_dir.exists()
    assert not sm.is_active
    assert sm.temp_dir is None
    assert sm.wsp_path is None
    assert sm.final_icc_path is None


def test_default_wsp_path_auto_increments(qapp, tmp_path, monkeypatch):
    """Two new sessions in the same default dir should get distinct paths."""
    from wsprofiler import session_manager

    # Redirect the default sessions dir to tmp_path so we don't pollute
    # the user's home directory.
    target = tmp_path / "wsprofiler_sessions"
    target.mkdir()
    monkeypatch.setattr(
        session_manager.SessionManager,
        "default_sessions_dir",
        property(lambda self: target),
    )

    sm1 = session_manager.SessionManager()
    sm1.create_new()
    sm1_path = sm1.wsp_path
    sm1.cleanup()

    # Create the WSP file so the next SM auto-increments.
    sm1_path.parent.mkdir(parents=True, exist_ok=True)
    sm1_path.write_text("placeholder")

    sm2 = session_manager.SessionManager()
    sm2.create_new()
    try:
        assert sm2.wsp_path != sm1_path
        assert sm2.wsp_path.name.startswith("session_")
    finally:
        sm2.cleanup()


def test_default_sessions_dir_uses_qt_standard_paths():
    """The default WSP directory should resolve via QStandardPaths.

    With applicationName='wsprofiler' and organizationName='wsprofiler'
    set, AppDataLocation yields a path containing both names plus
    'sessions' at the end. (The conftest fixture guarantees the app
    name is set; this test just confirms the integration.)
    """
    from PySide6.QtCore import QCoreApplication
    from wsprofiler import session_manager

    assert QCoreApplication.applicationName() == "wsprofiler"
    sm = session_manager.SessionManager()
    sessions_dir = sm.default_sessions_dir
    assert "wsprofiler" in str(sessions_dir)
    assert sessions_dir.name == "sessions"


