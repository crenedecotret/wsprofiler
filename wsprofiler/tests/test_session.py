"""Tests for session save/load."""

import json
import zipfile
from pathlib import Path

import pytest

from wsprofiler.session import (
    CURRENT_VERSION,
    AutoState,
    DoneState,
    GenerateState,
    MeasureState,
    SimpleSnapshot,
    WizardSnapshot,
    WIZARD_TYPE_SIMPLE,
    WIZARD_TYPE_TWO_STEP,
    _dict_to_simple_snapshot,
    load_archive,
    load_session,
    save_session,
)


def test_round_trip(tmp_path: Path):
    base = tmp_path / "workspace"
    base.mkdir()

    # Create dummy files
    (base / "chart1.ti2").write_text("ti2 data")
    (base / "chart1.ti3").write_text("ti3 data")

    snapshot = WizardSnapshot(
        version=CURRENT_VERSION,
        current_page=1,
        target_name="mytarget",
        workspace=str(base),
        generate=GenerateState(device="i1", paper="A4", target_path=str(base / "mytarget")),
        measure1=MeasureState(
            chart_path=str(base / "chart1.ti2"),
            current_strip=5,
            measured_strips=[1, 2, 3, 4, 5],
            is_complete=True,
        ),
        measure2=MeasureState(chart_path=None, current_strip=None),
        auto1=AutoState(show_output=False),
        auto2=AutoState(show_output=True),
        done=DoneState(final_icc_path=None),
    )

    file_paths = {
        "chart1_ti2": base / "chart1.ti2",
        "chart1_ti3": base / "chart1.ti3",
    }

    wsprof = tmp_path / "session.wsp2"
    save_session(wsprof, snapshot, base, file_paths)
    assert wsprof.exists()

    # Load into a fresh directory
    output = tmp_path / "extracted"
    loaded = load_session(wsprof, output)

    assert loaded.version == CURRENT_VERSION
    assert loaded.current_page == 1
    assert loaded.target_name == "mytarget"
    assert loaded.generate.device == "i1"
    assert loaded.generate.paper == "A4"
    assert loaded.measure1.is_complete is True
    assert loaded.measure1.measured_strips == [1, 2, 3, 4, 5]
    assert loaded.measure2.is_complete is False

    # Files should be resolved to absolute paths in output dir
    assert "chart1_ti2" in loaded.files
    assert Path(loaded.files["chart1_ti2"]).exists()
    assert Path(loaded.files["chart1_ti3"]).exists()


def test_version_mismatch(tmp_path: Path):
    wsprof = tmp_path / "bad.wsp2"
    import zipfile
    import json

    bad_manifest = json.dumps({"version": 999, "current_page": 0})
    with zipfile.ZipFile(wsprof, "w") as zf:
        zf.writestr("manifest.json", bad_manifest)

    with pytest.raises(ValueError, match="Unsupported session version"):
        load_session(wsprof, tmp_path / "out")


def test_missing_file_skipped(tmp_path: Path):
    base = tmp_path / "workspace"
    base.mkdir()
    (base / "exists.ti2").write_text("data")

    snapshot = WizardSnapshot(
        version=CURRENT_VERSION,
        current_page=0,
        target_name="t",
        workspace=str(base),
        generate=GenerateState(device="i1", paper="A4", target_path=str(base / "t")),
        measure1=MeasureState(chart_path=None, current_strip=None),
        measure2=MeasureState(chart_path=None, current_strip=None),
        auto1=AutoState(),
        auto2=AutoState(),
        done=DoneState(),
    )

    file_paths = {
        "exists": base / "exists.ti2",
        "missing": base / "missing.ti3",
    }

    wsprof = tmp_path / "session.wsp2"
    save_session(wsprof, snapshot, base, file_paths)

    loaded = load_session(wsprof, tmp_path / "out")
    assert "exists" in loaded.files
    assert "missing" not in loaded.files


def test_simple_snapshot_round_trip(tmp_path: Path):
    """SimpleSnapshot can be saved and read back via load_archive."""
    base = tmp_path / "workspace"
    base.mkdir()

    (base / "mytarget.ti1").write_text("ti1 data")
    (base / "mytarget.ti2").write_text("ti2 data")
    (base / "mytarget.ti3").write_text("ti3 data")
    (base / "mytarget.icc").write_text("icc data")
    (base / "mytarget_01.tif").write_text("tiff p1")
    (base / "mytarget_02.tif").write_text("tiff p2")

    snapshot = SimpleSnapshot(
        version=CURRENT_VERSION,
        wizard_type=WIZARD_TYPE_SIMPLE,
        target_name="mytarget",
        workspace=str(base),
        generated_at="2026-06-28T12:00:00+00:00",
    )

    file_paths = {
        "ti1": base / "mytarget.ti1",
        "ti2": base / "mytarget.ti2",
        "ti3": base / "mytarget.ti3",
        "icc": base / "mytarget.icc",
        "tiff_1": base / "mytarget_01.tif",
        "tiff_2": base / "mytarget_02.tif",
    }

    wsp = tmp_path / "mytarget.wsp"
    save_session(wsp, snapshot, base, file_paths)
    assert wsp.exists()

    extract = tmp_path / "extracted"
    manifest, files = load_archive(wsp, extract)

    assert manifest["wizard_type"] == WIZARD_TYPE_SIMPLE
    assert manifest["target_name"] == "mytarget"
    assert manifest["workspace"] == str(base)
    assert manifest["generated_at"] == "2026-06-28T12:00:00+00:00"
    assert "ti1" in files
    assert "ti2" in files
    assert "ti3" in files
    assert "icc" in files
    assert "tiff_1" in files
    assert "tiff_2" in files
    assert files["ti2"].read_text() == "ti2 data"


def test_simple_snapshot_default_wizard_type(tmp_path: Path):
    """load_session rejects a simple-wizard archive."""
    base = tmp_path / "workspace"
    base.mkdir()
    (base / "x.ti2").write_text("x")

    snapshot = SimpleSnapshot(
        version=CURRENT_VERSION,
        wizard_type=WIZARD_TYPE_SIMPLE,
        target_name="x",
        workspace=str(base),
    )
    wsp = tmp_path / "x.wsp"
    save_session(wsp, snapshot, base, {"ti2": base / "x.ti2"})

    with pytest.raises(ValueError, match="Cannot load a 'simple' archive"):
        load_session(wsp, tmp_path / "out")


def test_wizard_snapshot_default_type(tmp_path: Path):
    """Existing WizardSnapshot manifests default to 'two_step' for back-compat."""
    manifest = json.dumps({
        "version": CURRENT_VERSION,
        "current_page": 0,
        "target_name": "x",
        "workspace": str(tmp_path),
        "generate": {"device": "i1", "paper": "A4", "target_path": "x"},
        "measure1": {"chart_path": None, "current_strip": None,
                     "measured_strips": [], "is_complete": False},
        "measure2": {"chart_path": None, "current_strip": None,
                     "measured_strips": [], "is_complete": False},
        "auto1": {"show_output": False},
        "auto2": {"show_output": False},
        "done": {"final_icc_path": None},
    })
    wsp = tmp_path / "legacy.wsp2"
    with zipfile.ZipFile(wsp, "w") as zf:
        zf.writestr("manifest.json", manifest)

    loaded = load_session(wsp, tmp_path / "out")
    assert loaded.wizard_type == WIZARD_TYPE_TWO_STEP


def test_wizard_snapshot_explicit_type(tmp_path: Path):
    """WizardSnapshot carries wizard_type through the manifest."""
    base = tmp_path / "workspace"
    base.mkdir()
    (base / "c.ti2").write_text("x")

    snapshot = WizardSnapshot(
        version=CURRENT_VERSION,
        current_page=0,
        target_name="c",
        workspace=str(base),
        generate=GenerateState(device="i1", paper="A4", target_path="c"),
        measure1=MeasureState(chart_path=None, current_strip=None),
        measure2=MeasureState(chart_path=None, current_strip=None),
        auto1=AutoState(),
        auto2=AutoState(),
        done=DoneState(),
        wizard_type=WIZARD_TYPE_TWO_STEP,
    )
    wsp = tmp_path / "c.wsp2"
    save_session(wsp, snapshot, base, {"chart1_ti2": base / "c.ti2"})

    with zipfile.ZipFile(wsp) as zf:
        manifest = json.loads(zf.read("manifest.json"))
    assert manifest["wizard_type"] == WIZARD_TYPE_TWO_STEP


def test_simple_snapshot_with_optimisation_fields(tmp_path: Path):
    """SimpleSnapshot with generate_config, profile_configs, and optimisation_count round-trips."""
    base = tmp_path / "workspace"
    base.mkdir()
    (base / "mytarget.ti2").write_text("ti2 data")

    snapshot = SimpleSnapshot(
        version=CURRENT_VERSION,
        wizard_type=WIZARD_TYPE_SIMPLE,
        target_name="mytarget",
        workspace=str(base),
        generated_at="2026-06-28T12:00:00+00:00",
        generate_config={"device": "i1", "paper": "A3", "pages": 1},
        profile_configs=[
            {"gamut_profile": "/path/to/clay.icc", "quality": "High", "smoothing": 0.75, "description": "Original"}
        ],
        optimisation_count=2,
    )

    file_paths = {"ti2": base / "mytarget.ti2"}
    wsp = tmp_path / "mytarget.wsp"
    save_session(wsp, snapshot, base, file_paths)

    manifest, files = load_archive(wsp, tmp_path / "extracted")
    assert manifest["generate_config"] == snapshot.generate_config
    assert manifest["profile_configs"] == snapshot.profile_configs
    assert manifest["optimisation_count"] == 2
    assert files["ti2"].read_text() == "ti2 data"


def test_simple_snapshot_backward_compat_v1(tmp_path: Path):
    """A v1 SimpleSnapshot manifest without new fields loads with defaults."""
    manifest = json.dumps({
        "version": 1,
        "wizard_type": WIZARD_TYPE_SIMPLE,
        "target_name": "legacy",
        "workspace": str(tmp_path),
        "generated_at": "2026-01-01T00:00:00+00:00",
        "files": {},
    })
    wsp = tmp_path / "legacy.wsp"
    with zipfile.ZipFile(wsp, "w") as zf:
        zf.writestr("manifest.json", manifest)

    loaded = _dict_to_simple_snapshot(json.loads(manifest))
    assert loaded.version == 1
    assert loaded.wizard_type == WIZARD_TYPE_SIMPLE
    assert loaded.generate_config == {}
    assert loaded.profile_configs == []
    assert loaded.optimisation_count == 0


def test_dict_to_simple_snapshot_parses_new_fields():
    """_dict_to_simple_snapshot correctly parses all new fields."""
    data = {
        "version": 1,
        "wizard_type": WIZARD_TYPE_SIMPLE,
        "target_name": "test",
        "workspace": "/tmp",
        "generated_at": "2026-06-28T12:00:00+00:00",
        "files": {"ti2": "test.ti2"},
        "generate_config": {"device": "3p", "paper": "A4"},
        "profile_configs": [
            {"gamut_profile": None, "quality": "High", "smoothing": 0.5, "description": "Test"}
        ],
        "optimisation_count": 3,
    }
    snap = _dict_to_simple_snapshot(data)
    assert snap.generate_config == {"device": "3p", "paper": "A4"}
    assert snap.profile_configs == [
        {"gamut_profile": None, "quality": "High", "smoothing": 0.5, "description": "Test"}
    ]
    assert snap.optimisation_count == 3
