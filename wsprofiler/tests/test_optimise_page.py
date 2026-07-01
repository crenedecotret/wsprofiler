"""Tests for OptimisePage snapshot/load logic."""

from pathlib import Path

import pytest

from wsprofiler.session import save_session, SimpleSnapshot, WIZARD_TYPE_SIMPLE
from wsprofiler.ui.session_controller import SimpleSessionController


def _ensure_app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_optimise_page_snapshot_round_trip(tmp_path: Path):
    """OptimisePage.snapshot() and apply_snapshot() preserve state."""
    from wsprofiler.ui.pages.optimise_page import OptimisePage

    _ensure_app()
    page = OptimisePage(workspace=tmp_path)

    page._generate_config = {"device": "i1", "paper": "A3", "pages": 2}
    page._profile_configs = [
        {"gamut_profile": "/path/clay.icc", "quality": "High", "smoothing": 0.5, "description": "Original"},
        {"gamut_profile": "/path/clay.icc", "quality": "High", "smoothing": 0.75, "description": "Opt1"},
    ]
    page._optimisation_passes = [
        {"ti1": Path("a.ti1"), "ti2": Path("a.ti2"), "ti3": Path("a.ti3"), "icc": Path("a.icc")}
    ]
    page._combined_ti3s = [Path("combined1.ti3")]

    snap = page.snapshot()
    assert snap["generate_config"]["device"] == "i1"
    assert snap["optimisation_count"] == 1
    assert len(snap["profile_configs"]) == 2

    # Apply to fresh page
    page2 = OptimisePage(workspace=tmp_path)
    page2.apply_snapshot(snap)
    assert page2._generate_config == page._generate_config
    assert page2._profile_configs == page._profile_configs


def test_optimise_page_load_wsp_with_optimisations(tmp_path: Path):
    """OptimisePage.load_wsp_session restores original + optimisation passes."""
    from wsprofiler.ui.pages.optimise_page import OptimisePage

    _ensure_app()
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    # Create dummy files
    (workspace / "mytarget.ti2").write_text("ti2")
    (workspace / "mytarget.ti3").write_text("ti3")
    (workspace / "mytarget.icc").write_text("icc")
    (workspace / "mytarget_opt1.ti2").write_text("opt1 ti2")
    (workspace / "mytarget_opt1.ti3").write_text("opt1 ti3")
    (workspace / "mytarget_opt1.icc").write_text("opt1 icc")
    (workspace / "mytarget_combined1.ti3").write_text("combined")

    snapshot = SimpleSnapshot(
        version=1,
        wizard_type=WIZARD_TYPE_SIMPLE,
        target_name="mytarget",
        workspace=str(workspace),
        generate_config={"device": "i1", "paper": "A3", "pages": 1},
        profile_configs=[
            {"gamut_profile": None, "quality": "High", "smoothing": 0.5, "description": "Original"}
        ],
        optimisation_count=1,
    )

    file_paths = {
        "ti2": workspace / "mytarget.ti2",
        "ti3": workspace / "mytarget.ti3",
        "icc": workspace / "mytarget.icc",
        "pass1_ti2": workspace / "mytarget_opt1.ti2",
        "pass1_ti3": workspace / "mytarget_opt1.ti3",
        "pass1_icc": workspace / "mytarget_opt1.icc",
        "combined1_ti3": workspace / "mytarget_combined1.ti3",
    }

    wsp = tmp_path / "mytarget.wsp"
    save_session(wsp, snapshot, workspace, file_paths)

    page = OptimisePage(workspace=workspace)
    page.load_wsp_session(wsp)

    assert page.has_data()
    assert page._generate_config["device"] == "i1"
    assert len(page._optimisation_passes) == 1
    assert page._optimisation_passes[0]["ti2"] == workspace / "mytarget_opt1.ti2"
    assert page._combined_ti3s[0] == workspace / "mytarget_combined1.ti3"


def test_optimise_page_load_wsp_backward_compat(tmp_path: Path):
    """OptimisePage.load_wsp_session handles old v1 .wsp without optimisation data."""
    from wsprofiler.ui.pages.optimise_page import OptimisePage

    _ensure_app()
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    (workspace / "mytarget.ti2").write_text("ti2")
    (workspace / "mytarget.ti3").write_text("ti3")
    (workspace / "mytarget.icc").write_text("icc")

    snapshot = SimpleSnapshot(
        version=1,
        wizard_type=WIZARD_TYPE_SIMPLE,
        target_name="mytarget",
        workspace=str(workspace),
    )

    wsp = tmp_path / "mytarget.wsp"
    save_session(
        wsp,
        snapshot,
        workspace,
        {
            "ti2": workspace / "mytarget.ti2",
            "ti3": workspace / "mytarget.ti3",
            "icc": workspace / "mytarget.icc",
        },
    )

    page = OptimisePage(workspace=workspace)
    page.load_wsp_session(wsp)

    assert page.has_data()
    assert page._optimisation_passes == []
    assert page._combined_ti3s == []
    assert page._generate_config == {}


def test_patch_count_spin_default(tmp_path: Path):
    """Patch count spinbox is seeded from _PATCHES_PER_PAGE when config is set."""
    from wsprofiler.ui.pages.optimise_page import OptimisePage

    _ensure_app()
    page = OptimisePage(workspace=tmp_path)

    assert page._patch_count_spin is not None
    assert page._patch_count_spin.minimum() == 50
    assert page._patch_count_spin.maximum() == 1000
    # Default before config is set
    assert page._patch_count_spin.value() == 400

    # After setting config and calling _update_ready_status, value should
    # come from _PATCHES_PER_PAGE
    page._generate_config = {"device": "i1", "paper": "A3", "double_density": False, "no_border": False}
    page._update_ready_status()

    from wsprofiler.ui.pages.generate_page import _PATCHES_PER_PAGE
    expected = _PATCHES_PER_PAGE.get(("i1", "A3", False, False), 400)
    assert page._patch_count_spin.value() == expected


def test_pass_target_stem_naming(tmp_path: Path):
    """_pass_target_stem produces session_opt<N> inside the page's workspace."""
    from wsprofiler.ui.pages.optimise_page import OptimisePage

    _ensure_app()
    page = OptimisePage(workspace=tmp_path)
    # The Wizard sets the workspace to the SessionManager's temp dir via
    # set_workspace(); the page's ``workspace`` attribute is what
    # _pass_target_stem uses.
    page.set_workspace(tmp_path)

    assert page._pass_target_stem(1) == tmp_path / "session_opt1"
    assert page._pass_target_stem(2) == tmp_path / "session_opt2"
    assert page._pass_target_stem(10) == tmp_path / "session_opt10"


def test_latest_icc_and_ti3(tmp_path: Path):
    """_latest_icc_path and _latest_ti3_path return the most recent files."""
    from wsprofiler.ui.pages.optimise_page import OptimisePage

    _ensure_app()
    page = OptimisePage(workspace=tmp_path)
    page._original_pass = {
        "icc": tmp_path / "orig.icc",
        "ti3": tmp_path / "orig.ti3",
    }

    assert page._latest_icc_path() == tmp_path / "orig.icc"
    assert page._latest_ti3_path() == tmp_path / "orig.ti3"

    page._optimisation_passes.append({
        "icc": tmp_path / "opt1.icc",
        "ti3": tmp_path / "opt1.ti3",
    })

    assert page._latest_icc_path() == tmp_path / "opt1.icc"
    assert page._latest_ti3_path() == tmp_path / "opt1.ti3"
