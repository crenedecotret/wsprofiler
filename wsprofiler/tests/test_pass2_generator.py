"""Tests for wsprofiler.profiling.pass2_generator.

All tests use a stub predictor so no Argyll installation is required.
The stub maps RGB→Lab as:  L* = R*100, a* = (G-0.5)*100, b* = (B-0.5)*100
which is linear, invertible, and fills a reasonable Lab range.
"""
from __future__ import annotations

import math
import tempfile
from pathlib import Path

import numpy as np
import pytest

from wsprofiler.profiling.pass2_generator import (
    Candidate,
    _delta_e_to_nearest,
    _FORCED_RGB,
    build_candidate_pool,
    generate_pass2_ti1,
    load_pass1_lab,
    select_patches,
    write_ti1,
)
from wsprofiler.ti import cgats


# ---------------------------------------------------------------------------
# Stub predictor
# ---------------------------------------------------------------------------

def _stub_predictor(rgb: np.ndarray) -> np.ndarray:
    """Deterministic RGB→Lab stub. No external tools required."""
    lab = np.empty_like(rgb)
    lab[:, 0] = rgb[:, 0] * 100.0          # L* ∈ [0, 100]
    lab[:, 1] = (rgb[:, 1] - 0.5) * 100.0  # a* ∈ [-50, 50]
    lab[:, 2] = (rgb[:, 2] - 0.5) * 100.0  # b* ∈ [-50, 50]
    return lab


# ---------------------------------------------------------------------------
# Minimal Pass-1 TI3 fixture
# ---------------------------------------------------------------------------

_TI3_CONTENT = """\
CTI3

DESCRIPTOR "Test pass-1 measurements"
ORIGINATOR "test"
CREATED "Mon Jan  1 00:00:00 2024"
COLOR_REP "RGB_XYZ"

NUMBER_OF_FIELDS 8
BEGIN_DATA_FORMAT
SAMPLE_ID SAMPLE_LOC RGB_R RGB_G RGB_B XYZ_X XYZ_Y XYZ_Z
END_DATA_FORMAT

NUMBER_OF_SETS 6
BEGIN_DATA
1 "A1" 100.0 100.0 100.0 85.15 88.61 73.61
2 "A2" 0.00 0.00 0.00 2.08 2.09 2.02
3 "A3" 50.0 50.0 50.0 18.0 19.0 16.5
4 "A4" 100.0 0.00 0.00 15.0 8.0 1.5
5 "A5" 0.00 100.0 0.00 10.0 22.0 4.0
6 "A6" 0.00 0.00 100.0 3.0 3.5 32.0
END_DATA
"""


@pytest.fixture
def pass1_ti3(tmp_path: Path) -> Path:
    p = tmp_path / "pass1.ti3"
    p.write_text(_TI3_CONTENT)
    return p


# ---------------------------------------------------------------------------
# 1. Candidate pool is deterministic
# ---------------------------------------------------------------------------

def test_candidate_pool_deterministic():
    pool_a = build_candidate_pool(grid=5, halton_n=64, neutrals=9, edge_steps=5)
    pool_b = build_candidate_pool(grid=5, halton_n=64, neutrals=9, edge_steps=5)
    assert len(pool_a) == len(pool_b)
    for ca, cb in zip(pool_a, pool_b):
        assert ca.rgb == cb.rgb
        assert ca.source == cb.source


# ---------------------------------------------------------------------------
# 2. Pool has expected minimum size and all sources present
# ---------------------------------------------------------------------------

def test_candidate_pool_sources():
    # edge_steps=11 ensures intermediate edge points don't coincide with grid=5 steps
    pool = build_candidate_pool(grid=5, halton_n=64, neutrals=9, edge_steps=11)
    sources = {c.source for c in pool}
    assert sources >= {"grid", "halton", "neutral", "edge", "hue_sweep", "anchor"}


# ---------------------------------------------------------------------------
# 3. Greedy selection: count, min ΔE, forced anchors present
# ---------------------------------------------------------------------------

def test_select_patches_count_and_min_de(pass1_ti3: Path):
    candidates = build_candidate_pool(grid=7, halton_n=128, neutrals=9, edge_steps=5)
    pass1_lab = load_pass1_lab(pass1_ti3, _stub_predictor)

    target_n = 30
    selected = select_patches(
        candidates,
        _stub_predictor,
        pass1_lab,
        target_n,
        min_dE=2.5,
    )

    assert len(selected) == target_n

    # Verify min ΔE between every pair of selected patches
    sel_rgb = np.array([c.rgb for c in selected])
    sel_lab = _stub_predictor(sel_rgb)
    for i in range(len(sel_lab)):
        for j in range(i + 1, len(sel_lab)):
            dE = math.sqrt(sum((sel_lab[i, k] - sel_lab[j, k]) ** 2 for k in range(3)))
            assert dE >= 2.5 - 1e-6, f"ΔE={dE:.3f} between patch {i} and {j} is below min"


def test_white_and_black_forced(pass1_ti3: Path):
    candidates = build_candidate_pool(grid=5, halton_n=64, neutrals=9, edge_steps=5)
    pass1_lab = load_pass1_lab(pass1_ti3, _stub_predictor)
    selected = select_patches(candidates, _stub_predictor, pass1_lab, 20, min_dE=2.5)

    rgb_set = {(round(c.rgb[0] * 255), round(c.rgb[1] * 255), round(c.rgb[2] * 255))
               for c in selected}
    forced_set = {(round(r * 255), round(g * 255), round(b * 255)) for r, g, b in _FORCED_RGB}
    # At least white and black should be included
    assert (255, 255, 255) in rgb_set
    assert (0, 0, 0) in rgb_set


# ---------------------------------------------------------------------------
# 4. TI1 format roundtrip
# ---------------------------------------------------------------------------

def test_ti1_format_roundtrip(tmp_path: Path):
    patches = [
        Candidate(rgb=(1.0, 1.0, 1.0), source="anchor", tags={"kind": "anchor"}),
        Candidate(rgb=(0.0, 0.0, 0.0), source="anchor", tags={"kind": "anchor"}),
        Candidate(rgb=(0.5, 0.25, 0.75), source="grid", tags={"kind": "grid"}),
    ]
    out = tmp_path / "test.ti1"
    write_ti1(out, patches, created="Mon Jan  1 00:00:00 2024")

    table = cgats.load(out)
    assert table.keywords.get("NUMBER_OF_SETS") == "3"
    assert "SAMPLE_ID" in table.data_format
    assert "RGB_R" in table.data_format
    assert "RGB_G" in table.data_format
    assert "RGB_B" in table.data_format
    assert len(table.data) == 3

    # White patch: RGB_R == 100.0
    assert abs(float(table.data[0]["RGB_R"]) - 100.0) < 0.01
    # Black patch: all zeros
    for ch in ("RGB_R", "RGB_G", "RGB_B"):
        assert abs(float(table.data[1][ch])) < 0.01
    # Mixed patch
    assert abs(float(table.data[2]["RGB_R"]) - 50.0) < 0.1
    assert abs(float(table.data[2]["RGB_G"]) - 25.0) < 0.1
    assert abs(float(table.data[2]["RGB_B"]) - 75.0) < 0.1


# ---------------------------------------------------------------------------
# 5. Pass-1 influence: selected patches are more novel than a random slice
# ---------------------------------------------------------------------------

def test_pass1_influence(pass1_ti3: Path):
    """Selected patches should have higher mean nearest-ΔE to Pass-1 than pool average."""
    candidates = build_candidate_pool(grid=7, halton_n=128, neutrals=9, edge_steps=5)
    pass1_lab = load_pass1_lab(pass1_ti3, _stub_predictor)

    selected = select_patches(
        candidates, _stub_predictor, pass1_lab, target_n=40, min_dE=2.5
    )

    # Mean nearest-ΔE of selected vs pass1
    sel_rgb = np.array([c.rgb for c in selected])
    sel_lab = _stub_predictor(sel_rgb)
    mean_sel_dE = _delta_e_to_nearest(sel_lab, pass1_lab).mean()

    # Mean nearest-ΔE of pool first 40 (naively ordered) vs pass1
    pool_rgb = np.array([c.rgb for c in candidates[:40]])
    pool_lab = _stub_predictor(pool_rgb)
    mean_naive_dE = _delta_e_to_nearest(pool_lab, pass1_lab).mean()

    assert mean_sel_dE > mean_naive_dE, (
        f"Selected mean dE ({mean_sel_dE:.2f}) should exceed naive slice mean ({mean_naive_dE:.2f})"
    )


# ---------------------------------------------------------------------------
# 6. generate_pass2_ti1 end-to-end with stub (no xicclu)
# ---------------------------------------------------------------------------

def test_generate_pass2_ti1_stub(tmp_path: Path, pass1_ti3: Path, monkeypatch):
    """End-to-end test injecting stub predictor instead of xicclu."""
    from wsprofiler.profiling import pass2_generator as pg

    # Monkeypatch XicclLabPredictor to use the stub
    class _StubPredictor:
        def __init__(self, *a, **kw):
            pass

        def lab_batch(self, rgb: np.ndarray) -> np.ndarray:
            return _stub_predictor(rgb)

        def __call__(self, rgb: np.ndarray) -> np.ndarray:
            return self.lab_batch(rgb)

    monkeypatch.setattr(pg, "XicclLabPredictor", _StubPredictor)

    out_ti1 = tmp_path / "chart2.ti1"
    result = pg.generate_pass2_ti1(
        precond_icc=Path("/fake/precond.icc"),
        pass1_ti3=pass1_ti3,
        out_ti1=out_ti1,
        target_n=25,
        xicclu_path=Path("/fake/xicclu"),
        min_dE=2.5,
        grid=5,
        halton_n=64,
        neutrals=9,
        edge_steps=5,
        created="Mon Jan  1 00:00:00 2024",
    )

    assert result == out_ti1
    assert out_ti1.exists()
    table = cgats.load(out_ti1)
    assert int(table.keywords["NUMBER_OF_SETS"]) == 25
