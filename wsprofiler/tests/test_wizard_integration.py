"""Tests for the Wizard integration with SessionManager.

These tests exercise the full wizard flow without blocking on modal dialogs
by setting ``_suppress_dialogs = True`` on the wizard under test.
"""
from __future__ import annotations

import os

# Force offscreen Qt platform BEFORE any PySide6 import.
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import zipfile
from pathlib import Path

import pytest

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication


def _ensure_app():
    return QApplication.instance() or QApplication([])


def _wizard_with_suppressed_dialogs(
    workspace: Path,
    dest: Path | None = None,
):
    """Create a Wizard with dialogs suppressed.

    If ``dest`` is provided, the GeneratePage's ICC destination is set
    *before* the wizard creates its initial session, so the session
    captures it. The wizard is fully active by the time it returns.
    """
    from wsprofiler.ui.wizard import Wizard
    # We need to set the destination before the wizard's __init__
    # captures the generate-page value. Monkey-patch the property
    # by creating a GeneratePage first, then the wizard.
    if dest is not None:
        # Create a temporary wizard first to access the GeneratePage,
        # set the text, then call _initialise_session() manually so
        # the destination is captured. This avoids building pages twice.
        wiz = Wizard.__new__(Wizard)
        from PySide6.QtWidgets import QHBoxLayout, QListWidget, QStackedWidget
        from wsprofiler.session_manager import SessionManager
        QWidget = __import__("PySide6.QtWidgets", fromlist=["QWidget"]).QWidget
        QVBoxLayout = __import__("PySide6.QtWidgets", fromlist=["QVBoxLayout"]).QVBoxLayout
        from wsprofiler.ui.pages.generate_page import GeneratePage
        from wsprofiler.ui.pages.measurement_page import MeasurementPage
        from wsprofiler.ui.pages.optimise_page import OptimisePage
        from wsprofiler.ui.pages.profile_page import ProfilePage

        # Initialise the QWidget base class without going through __init__.
        QWidget.__init__(wiz)
        wiz.workspace = workspace
        wiz._session = SessionManager()
        wiz._suppress_dialogs = True
        wiz._pending_ti2 = None

        # Replicate __init__'s layout / pages setup
        layout = QHBoxLayout(wiz)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        left = QWidget()
        left.setFixedWidth(180)
        left_lyt = QVBoxLayout(left)
        left_lyt.setContentsMargins(0, 0, 0, 0)
        left_lyt.setSpacing(4)
        wiz.steps = QListWidget()
        wiz.steps.setAlternatingRowColors(True)
        left_lyt.addWidget(wiz.steps, stretch=1)
        from PySide6.QtWidgets import QPushButton
        wiz._load_session_btn = QPushButton("Session Manager")
        left_lyt.addWidget(wiz._load_session_btn)
        layout.addWidget(left)

        wiz.stack = QStackedWidget()
        layout.addWidget(wiz.stack, stretch=1)

        wiz._pages = {
            "Generate": GeneratePage(workspace=workspace),
            "Measure": MeasurementPage(workspace=workspace),
            "Profile": ProfilePage(workspace=workspace),
            "Optimise": OptimisePage(workspace=workspace),
        }
        from PySide6.QtWidgets import QListWidgetItem
        for title, page in wiz._pages.items():
            QListWidgetItem(title, wiz.steps)
            wiz.stack.addWidget(page)
        wiz.steps.currentRowChanged.connect(wiz._on_step_changed)
        wiz.steps.setCurrentRow(list(wiz._pages.keys()).index("Generate"))

        # Set the destination BEFORE initialising the session so it
        # gets captured.
        wiz._pages["Generate"].target_edit.setText(str(dest))

        # Now call the same _initialise_session() the real __init__ does.
        wiz._initialise_session()
        wiz._update_step_availability()

        wiz._pages["Generate"].chartGenerated.connect(wiz._on_chart_generated)
        wiz._pages["Measure"].measurementsComplete.connect(wiz._on_measurements_complete)
        wiz._pages["Profile"].profileGenerated.connect(wiz._on_profile_generated)
        wiz._pages["Optimise"].optimisationComplete.connect(wiz._on_optimisation_complete)
        return wiz
    else:
        wiz = Wizard(workspace=workspace)
        wiz._suppress_dialogs = True
        return wiz


def _make_chart_files(wizard, with_ti3: bool = False, with_icc: bool = False):
    """Create dummy ti1/ti2/tiff files in the wizard's temp dir."""
    target = wizard.session.target_path
    assert target is not None
    stem = target.name
    (target.parent / f"{stem}.ti1").write_text("ti1 data")
    (target.parent / f"{stem}.ti2").write_text("ti2 data")
    (target.parent / f"{stem}_01.tif").write_text("tiff p1")
    (target.parent / f"{stem}_02.tif").write_text("tiff p2")
    if with_ti3:
        (target.parent / f"{stem}.ti3").write_text("ti3 data")
    if with_icc:
        (target.parent / f"{stem}.icc").write_text("icc data")


def _item_is_enabled(wizard, title: str) -> bool:
    idx = list(wizard._pages.keys()).index(title)
    item = wizard.steps.item(idx)
    return bool(item.flags() & Qt.ItemIsEnabled)


# ----------------------------------------------------------------- flow

def test_wizard_constructor_creates_session(tmp_path: Path, qapp):
    """The wizard should always have a live session after construction."""
    wiz = _wizard_with_suppressed_dialogs(tmp_path)
    try:
        assert wiz.session.is_active
        assert wiz.session.temp_dir.exists()
        assert wiz.session.target_path == wiz.session.temp_dir / wiz.session.target_stem
    finally:
        wiz.session.cleanup()


def test_wizard_on_chart_generated_saves_wsp(tmp_path: Path, qapp):
    wiz = _wizard_with_suppressed_dialogs(tmp_path)
    try:
        _make_chart_files(wiz)
        stem = wiz.session.target_stem
        ti2 = wiz.session.target_path.with_suffix(".ti2")
        wiz._on_chart_generated(ti2)
        # WSP should exist and contain the ti1/ti2/tiff files.
        assert wiz.session.wsp_path.exists()
        with zipfile.ZipFile(wiz.session.wsp_path) as zf:
            names = set(zf.namelist())
        assert f"{stem}.ti1" in names
        assert f"{stem}.ti2" in names
        assert f"{stem}_01.tif" in names
        # The wizard should have advanced to the Measure page.
        assert wiz.stack.currentWidget() is wiz._pages["Measure"]
    finally:
        wiz.session.cleanup()


def test_wizard_on_measurements_complete_saves_ti3(tmp_path: Path, qapp):
    wiz = _wizard_with_suppressed_dialogs(tmp_path)
    try:
        _make_chart_files(wiz)
        # Simulate chart generation (gets us to Measure page + WSP).
        wiz._on_chart_generated(wiz.session.target_path.with_suffix(".ti2"))
        # Simulate ti3 being written.
        ti3 = wiz.session.target_path.with_suffix(".ti3")
        ti3.write_text("ti3 data")
        wiz._on_measurements_complete(ti3)
        # WSP should now contain ti3.
        with zipfile.ZipFile(wiz.session.wsp_path) as zf:
            names = set(zf.namelist())
        assert f"{wiz.session.target_stem}.ti3" in names
        # Wizard should have advanced to Profile page.
        assert wiz.stack.currentWidget() is wiz._pages["Profile"]
    finally:
        wiz.session.cleanup()


def test_wizard_on_profile_generated_copies_icc(tmp_path: Path, qapp):
    dest = tmp_path / "output" / "myprinter.icc"
    wiz = _wizard_with_suppressed_dialogs(tmp_path, dest=dest)
    try:
        _make_chart_files(wiz, with_ti3=True)
        stem = wiz.session.target_stem
        # Drive the full flow up to profile generation.
        wiz._on_chart_generated(wiz.session.target_path.with_suffix(".ti2"))
        wiz._on_measurements_complete(wiz.session.target_path.with_suffix(".ti3"))
        # Simulate colprof producing an ICC.
        icc = wiz.session.target_path.with_suffix(".icc")
        icc.write_text("icc data")
        wiz._on_profile_generated(icc)
        # The ICC should have been copied to the user-chosen destination.
        assert dest.exists()
        assert dest.read_text() == "icc data"
        # WSP should contain the ICC.
        with zipfile.ZipFile(wiz.session.wsp_path) as zf:
            names = set(zf.namelist())
        assert f"{stem}.icc" in names
        # Optimise page should now have data.
        opt = wiz._pages["Optimise"]
        assert opt.has_data()
    finally:
        wiz.session.cleanup()


def test_wizard_no_icc_destination_skips_copy(tmp_path: Path, qapp):
    wiz = _wizard_with_suppressed_dialogs(tmp_path)
    try:
        _make_chart_files(wiz, with_ti3=True)
        stem = wiz.session.target_stem
        # No destination set, so copy should be a no-op.
        wiz._on_chart_generated(wiz.session.target_path.with_suffix(".ti2"))
        wiz._on_measurements_complete(wiz.session.target_path.with_suffix(".ti3"))
        icc = wiz.session.target_path.with_suffix(".icc")
        icc.write_text("icc data")
        # Should not raise even though no destination is set.
        wiz._on_profile_generated(icc)
        # WSP still contains the ICC.
        with zipfile.ZipFile(wiz.session.wsp_path) as zf:
            names = set(zf.namelist())
        assert f"{stem}.icc" in names
    finally:
        wiz.session.cleanup()


# ------------------------------------------------------- load_wsp_session

def test_wizard_load_wsp_session_restores_state(tmp_path: Path, qapp):
    """Round-trip: save a WSP, then load it back into a fresh wizard."""
    dest = tmp_path / "output" / "myprinter.icc"
    wiz1 = _wizard_with_suppressed_dialogs(tmp_path, dest=dest)
    try:
        _make_chart_files(wiz1, with_ti3=True, with_icc=True)
        wiz1._on_chart_generated(wiz1.session.target_path.with_suffix(".ti2"))
        wiz1._on_measurements_complete(wiz1.session.target_path.with_suffix(".ti3"))
        wiz1._on_profile_generated(wiz1.session.target_path.with_suffix(".icc"))
        wsp_path = wiz1.session.wsp_path
    finally:
        wiz1.session.cleanup()

    # Now load in a new wizard
    wiz2 = _wizard_with_suppressed_dialogs(tmp_path)
    try:
        wiz2.load_wsp_session(wsp_path)
        # The session should be marked as loaded, with the same WSP path.
        assert wiz2.session.is_loaded
        assert wiz2.session.wsp_path == wsp_path
        # Wizard should have landed on the Optimise page (ICC exists).
        assert wiz2.stack.currentWidget() is wiz2._pages["Optimise"]
    finally:
        wiz2.session.cleanup()


def test_wizard_load_wsp_session_partial_ti3(tmp_path: Path, qapp):
    """Loading a WSP with ti3 but no icc should land on the Profile page."""
    dest = tmp_path / "out.icc"
    wiz1 = _wizard_with_suppressed_dialogs(tmp_path, dest=dest)
    try:
        _make_chart_files(wiz1, with_ti3=True)
        wiz1._on_chart_generated(wiz1.session.target_path.with_suffix(".ti2"))
        wiz1._on_measurements_complete(wiz1.session.target_path.with_suffix(".ti3"))
        wsp_path = wiz1.session.wsp_path
    finally:
        wiz1.session.cleanup()

    wiz2 = _wizard_with_suppressed_dialogs(tmp_path)
    try:
        wiz2.load_wsp_session(wsp_path)
        assert wiz2.stack.currentWidget() is wiz2._pages["Profile"]
    finally:
        wiz2.session.cleanup()


def test_wizard_load_wsp_session_only_ti2(tmp_path: Path, qapp):
    """Loading a WSP with only ti2 should land on the Measure page."""
    dest = tmp_path / "out.icc"
    wiz1 = _wizard_with_suppressed_dialogs(tmp_path, dest=dest)
    try:
        _make_chart_files(wiz1)
        wiz1._on_chart_generated(wiz1.session.target_path.with_suffix(".ti2"))
        wsp_path = wiz1.session.wsp_path
    finally:
        wiz1.session.cleanup()

    wiz2 = _wizard_with_suppressed_dialogs(tmp_path)
    try:
        wiz2.load_wsp_session(wsp_path)
        assert wiz2.stack.currentWidget() is wiz2._pages["Measure"]
    finally:
        wiz2.session.cleanup()


def test_wizard_load_old_format_wsp(tmp_path: Path, qapp):
    """Loading an old-format WSP (chart1_ti2 role key) lands on Measure."""
    import json, zipfile
    # Create a WSP with the old two-step role key format.
    wsp = tmp_path / "old_format.wsp"
    chart_dir = tmp_path / "old_charts"
    chart_dir.mkdir()
    (chart_dir / "chart1.ti2").write_text("ti2 data")
    manifest = {
        "version": 1, "wizard_type": "simple",
        "target_name": "chart1",
        "workspace": str(chart_dir),
        "generated_at": "2026-06-30T12:00:00+00:00",
        "files": {"chart1_ti2": "chart1.ti2"},
        "generate_config": {}, "profile_configs": [], "optimisation_count": 0,
    }
    with zipfile.ZipFile(wsp, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.write(chart_dir / "chart1.ti2", "chart1.ti2")

    wiz = _wizard_with_suppressed_dialogs(tmp_path)
    try:
        wiz.load_wsp_session(wsp)
        assert wiz.stack.currentWidget() is wiz._pages["Measure"]
    finally:
        wiz.session.cleanup()


# --------------------------------------------------- wizard Session Manager

def test_wizard_session_manager_button_exists(tmp_path: Path, qapp):
    """The wizard should expose a 'Session Manager' button in its sidebar."""
    wiz = _wizard_with_suppressed_dialogs(tmp_path)
    try:
        assert wiz._load_session_btn is not None
        assert wiz._load_session_btn.text() == "Session Manager"
    finally:
        wiz.session.cleanup()


def test_wizard_session_manager_button_triggers_load(tmp_path: Path, monkeypatch, qapp):
    """Clicking Session Manager should call Wizard.load_wsp_session."""
    # First create a WSP to load.
    dest = tmp_path / "out.icc"
    wiz1 = _wizard_with_suppressed_dialogs(tmp_path, dest=dest)
    try:
        _make_chart_files(wiz1)
        wiz1._on_chart_generated(wiz1.session.target_path.with_suffix(".ti2"))
        wsp_path = wiz1.session.wsp_path
    finally:
        wiz1.session.cleanup()

    wiz2 = _wizard_with_suppressed_dialogs(tmp_path)
    try:
        from PySide6.QtWidgets import QDialog
        from wsprofiler.ui import session_manager_dialog as smd_module

        class FakeSessionManagerDialog:
            def __init__(self, sessions_dir, parent=None):
                self._selected_path = wsp_path

            def exec(self):
                return QDialog.Accepted

            def selected_path(self):
                return self._selected_path

            def is_new_session(self):
                return False

        monkeypatch.setattr(
            smd_module, "SessionManagerDialog", FakeSessionManagerDialog
        )
        wiz2._on_load_session_clicked()
        assert wiz2.session.is_loaded
        assert wiz2.session.wsp_path == wsp_path
    finally:
        wiz2.session.cleanup()


# ---------------------------------------------------- step availability

def test_wizard_fresh_only_generate_enabled(tmp_path: Path, qapp):
    """On a fresh wizard only the Generate step should be clickable."""
    wiz = _wizard_with_suppressed_dialogs(tmp_path)
    try:
        assert _item_is_enabled(wiz, "Generate")
        assert not _item_is_enabled(wiz, "Measure")
        assert not _item_is_enabled(wiz, "Profile")
        assert not _item_is_enabled(wiz, "Optimise")
    finally:
        wiz.session.cleanup()


def test_wizard_measure_enabled_after_chart(tmp_path: Path, qapp):
    """After chart generation the Measure step becomes available."""
    wiz = _wizard_with_suppressed_dialogs(tmp_path)
    try:
        _make_chart_files(wiz)
        wiz._on_chart_generated(wiz.session.target_path.with_suffix(".ti2"))
        assert _item_is_enabled(wiz, "Generate")
        assert _item_is_enabled(wiz, "Measure")
        assert not _item_is_enabled(wiz, "Profile")
        assert not _item_is_enabled(wiz, "Optimise")
    finally:
        wiz.session.cleanup()


def test_wizard_profile_enabled_after_measure(tmp_path: Path, qapp):
    """After measurements the Profile step becomes available."""
    wiz = _wizard_with_suppressed_dialogs(tmp_path)
    try:
        _make_chart_files(wiz)
        wiz._on_chart_generated(wiz.session.target_path.with_suffix(".ti2"))
        ti3 = wiz.session.target_path.with_suffix(".ti3")
        ti3.write_text("ti3 data")
        wiz._on_measurements_complete(ti3)
        assert _item_is_enabled(wiz, "Generate")
        assert _item_is_enabled(wiz, "Measure")
        assert _item_is_enabled(wiz, "Profile")
        assert not _item_is_enabled(wiz, "Optimise")
    finally:
        wiz.session.cleanup()


def test_wizard_optimise_enabled_after_profile(tmp_path: Path, qapp):
    """After profile creation the Optimise step becomes available."""
    wiz = _wizard_with_suppressed_dialogs(tmp_path)
    try:
        _make_chart_files(wiz, with_ti3=True)
        wiz._on_chart_generated(wiz.session.target_path.with_suffix(".ti2"))
        wiz._on_measurements_complete(wiz.session.target_path.with_suffix(".ti3"))
        icc = wiz.session.target_path.with_suffix(".icc")
        icc.write_text("icc data")
        wiz._on_profile_generated(icc)
        assert _item_is_enabled(wiz, "Generate")
        assert _item_is_enabled(wiz, "Measure")
        assert _item_is_enabled(wiz, "Profile")
        assert _item_is_enabled(wiz, "Optimise")
    finally:
        wiz.session.cleanup()


def test_wizard_load_session_enables_appropriate_steps(tmp_path: Path, qapp):
    """Loading a partial session enables steps based on loaded state."""
    dest = tmp_path / "out.icc"
    wiz1 = _wizard_with_suppressed_dialogs(tmp_path, dest=dest)
    try:
        _make_chart_files(wiz1, with_ti3=True)
        wiz1._on_chart_generated(wiz1.session.target_path.with_suffix(".ti2"))
        wiz1._on_measurements_complete(wiz1.session.target_path.with_suffix(".ti3"))
        wsp_path = wiz1.session.wsp_path
    finally:
        wiz1.session.cleanup()

    wiz2 = _wizard_with_suppressed_dialogs(tmp_path)
    try:
        wiz2.load_wsp_session(wsp_path)
        assert _item_is_enabled(wiz2, "Generate")
        assert _item_is_enabled(wiz2, "Measure")
        assert _item_is_enabled(wiz2, "Profile")
        assert not _item_is_enabled(wiz2, "Optimise")
    finally:
        wiz2.session.cleanup()


# --------------------------------------------------- save_session_state (BUG 1)

def _read_manifest(wsp_path: Path):
    import json, zipfile
    with zipfile.ZipFile(wsp_path) as zf:
        return json.loads(zf.read("manifest.json")), set(zf.namelist())


def test_save_session_state_preserves_icc_and_profile_configs(tmp_path: Path, qapp):
    """save_session_state() after profiling must keep the icc + profile_configs.

    Regression for BUG 1: previously save_session_state hard-coded an empty
    profile_configs and never bundled the ICC, so any close/navigate-after-
    profile silently wiped the post-profile work from the WSP.
    """
    dest = tmp_path / "out.icc"
    wiz = _wizard_with_suppressed_dialogs(tmp_path, dest=dest)
    try:
        _make_chart_files(wiz, with_ti3=True)
        wiz._on_chart_generated(wiz.session.target_path.with_suffix(".ti2"))
        wiz._on_measurements_complete(wiz.session.target_path.with_suffix(".ti3"))
        icc = wiz.session.target_path.with_suffix(".icc")
        icc.write_text("icc data")
        wiz._on_profile_generated(icc)

        # Now trigger the bug path: navigate away from the Measure page,
        # which calls save_session_state(). The ICC and the profile config
        # must survive in the saved manifest.
        wiz._prev_step_index = list(wiz._pages.keys()).index("Measure")
        wiz.save_session_state()

        manifest, names = _read_manifest(wiz.session.wsp_path)
        stem = wiz.session.target_stem
        assert f"{stem}.icc" in names
        assert "icc" in manifest["files"]
        assert manifest["profile_configs"], "profile_configs should be non-empty after profiling"
        assert manifest["optimisation_count"] == 0
    finally:
        wiz.session.cleanup()


def test_save_session_state_after_optimise_preserves_passes(tmp_path: Path, qapp):
    """save_session_state() after an optimisation pass must keep all pass files."""
    dest = tmp_path / "out.icc"
    wiz = _wizard_with_suppressed_dialogs(tmp_path, dest=dest)
    try:
        _make_chart_files(wiz, with_ti3=True)
        wiz._on_chart_generated(wiz.session.target_path.with_suffix(".ti2"))
        wiz._on_measurements_complete(wiz.session.target_path.with_suffix(".ti3"))
        icc = wiz.session.target_path.with_suffix(".icc")
        icc.write_text("icc data")
        wiz._on_profile_generated(icc)

        # Simulate Optimise page having completed one pass.
        opt = wiz._pages["Optimise"]
        opt._optimisation_passes = [{"ti1": None, "ti2": None, "ti3": None, "icc": icc}]
        opt._combined_ti3s = []
        opt._profile_configs = [{"quality": "h"}]
        opt._generate_config = wiz._pages["Generate"].get_generate_config()
        # Force has_data() to be True via the original pass icc.
        opt._original_pass = {"icc": icc}

        wiz._prev_step_index = list(wiz._pages.keys()).index("Measure")
        wiz.save_session_state()

        manifest, names = _read_manifest(wiz.session.wsp_path)
        assert manifest["optimisation_count"] == 1
        assert "icc" in manifest["files"]
        assert len(manifest["profile_configs"]) == 1
    finally:
        wiz.session.cleanup()


def test_close_event_preserves_icc(tmp_path: Path, qapp):
    """MainWindow.closeEvent calls save_session_state; the ICC must survive."""
    from wsprofiler.ui.main_window import MainWindow
    from PySide6.QtGui import QCloseEvent

    mw = MainWindow(workspace=tmp_path)
    mw._wizard._suppress_dialogs = True
    try:
        wiz = mw._wizard
        _make_chart_files(wiz, with_ti3=True)
        wiz._on_chart_generated(wiz.session.target_path.with_suffix(".ti2"))
        wiz._on_measurements_complete(wiz.session.target_path.with_suffix(".ti3"))
        icc = wiz.session.target_path.with_suffix(".icc")
        icc.write_text("icc data")
        wiz._on_profile_generated(icc)
        wsp = wiz.session.wsp_path

        mw.closeEvent(QCloseEvent())

        manifest, names = _read_manifest(wsp)
        assert "icc" in manifest["files"]
        assert manifest["profile_configs"]
    finally:
        mw._wizard.session.cleanup()


# ------------------------------------------------------- precond bundling (BUG 3)

def _make_precond_profile(tmp_path: Path) -> Path:
    p = tmp_path / "precond" / "ref.icc"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("precond icc bytes")
    return p


def test_precond_icc_bundled_after_generate(tmp_path: Path, qapp):
    """A selected preconditioning profile is bundled in the WSP after Generate."""
    wiz = _wizard_with_suppressed_dialogs(tmp_path)
    try:
        precond = _make_precond_profile(tmp_path)
        gen = wiz._pages["Generate"]
        gen.precond_check.setChecked(True)
        gen.precond_path.setText(str(precond))

        _make_chart_files(wiz)
        wiz._on_chart_generated(wiz.session.target_path.with_suffix(".ti2"))

        manifest, names = _read_manifest(wiz.session.wsp_path)
        assert manifest["generate_config"]["precond_path"] == str(precond)
        assert "precond_icc" in manifest["files"]
        # The ICC bytes are bundled under the file's basename (flat arcname
        # for files outside the temp dir).
        assert precond.name in names
        # Inside the zip, the bundled file carries the original bytes.
        import zipfile
        with zipfile.ZipFile(wiz.session.wsp_path) as zf:
            assert zf.read(precond.name) == b"precond icc bytes"
    finally:
        wiz.session.cleanup()


def test_precond_icc_bundled_after_profile(tmp_path: Path, qapp):
    """The precond ICC survives through the profile step into the saved WSP."""
    dest = tmp_path / "out.icc"
    wiz = _wizard_with_suppressed_dialogs(tmp_path, dest=dest)
    try:
        precond = _make_precond_profile(tmp_path)
        gen = wiz._pages["Generate"]
        gen.precond_check.setChecked(True)
        gen.precond_path.setText(str(precond))

        _make_chart_files(wiz, with_ti3=True)
        wiz._on_chart_generated(wiz.session.target_path.with_suffix(".ti2"))
        wiz._on_measurements_complete(wiz.session.target_path.with_suffix(".ti3"))
        icc = wiz.session.target_path.with_suffix(".icc")
        icc.write_text("icc data")
        wiz._on_profile_generated(icc)

        manifest, _ = _read_manifest(wiz.session.wsp_path)
        assert manifest["generate_config"]["precond_path"] == str(precond)
        assert "precond_icc" in manifest["files"]
    finally:
        wiz.session.cleanup()


def test_no_precond_means_no_precond_role(tmp_path: Path, qapp):
    """Without a precond selection, the WSP has no precond_icc role."""
    wiz = _wizard_with_suppressed_dialogs(tmp_path)
    try:
        _make_chart_files(wiz)
        wiz._on_chart_generated(wiz.session.target_path.with_suffix(".ti2"))
        manifest, _ = _read_manifest(wiz.session.wsp_path)
        assert "precond_icc" not in manifest["files"]
        assert manifest["generate_config"].get("precond_path") == ""
    finally:
        wiz.session.cleanup()


def test_load_wsp_session_does_not_restore_precond_to_ui(tmp_path: Path, qapp):
    """Loading a WSP keeps the Generate precond field empty (chart already built)."""
    wiz1 = _wizard_with_suppressed_dialogs(tmp_path)
    try:
        precond = _make_precond_profile(tmp_path)
        gen = wiz1._pages["Generate"]
        gen.precond_check.setChecked(True)
        gen.precond_path.setText(str(precond))

        _make_chart_files(wiz1)
        wiz1._on_chart_generated(wiz1.session.target_path.with_suffix(".ti2"))
        wsp_path = wiz1.session.wsp_path
    finally:
        wiz1.session.cleanup()

    wiz2 = _wizard_with_suppressed_dialogs(tmp_path)
    try:
        wiz2.load_wsp_session(wsp_path)
        gen2 = wiz2._pages["Generate"]
        assert gen2.precond_path.text() == ""
        assert not gen2.precond_check.isChecked()
        # But the manifest still records which precond was used.
        manifest, names = _read_manifest(wsp_path)
        assert "precond_icc" in manifest["files"]
    finally:
        wiz2.session.cleanup()
