"""Pass-2 patch selector for two-pass ICC printer profiling.

Replaces ``targen … -c precond.icc`` for the second chart generation.
Given Pass-1 measurements (.ti3) and an intermediate ICC profile, it selects
a patch set that maximises new perceptual information gain over Pass 1.

Sections
--------
1. XicclLabPredictor  – RGB→Lab via Argyll ``xicclu``
2. Candidate pool     – build_candidate_pool()
3. Pass-1 ingestion   – load_pass1_lab()
4. Scoring            – score_candidates()
5. Greedy selection   – select_patches()
6. TI1 exporter       – write_ti1()
7. Entry point        – generate_pass2_ti1()
"""
from __future__ import annotations

import math
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Sequence

import numpy as np

from ..ti.ti3 import load_measured_patches


# ---------------------------------------------------------------------------
# Section 1 – RGB→Lab predictor via xicclu
# ---------------------------------------------------------------------------

class XicclLabPredictor:
    """Predict printed Lab from device RGB using an ICC profile and xicclu.

    Parameters
    ----------
    xicclu_path:
        Path to the Argyll ``xicclu`` binary.
    icc_path:
        Path to the ICC profile (forward, RGB→PCS).
    """

    def __init__(self, xicclu_path: Path, icc_path: Path) -> None:
        self._xicclu = str(xicclu_path)
        self._icc = str(icc_path)

    def lab_batch(self, rgb: np.ndarray) -> np.ndarray:
        """Convert N×3 RGB array (0..1) to N×3 Lab array.

        Launches a single xicclu subprocess for the entire batch.
        """
        # xicclu flags:
        #   -ir   forward (RGB → PCS)
        #   -pl   output Lab (D50)
        #   -s1   input scale 1.0 (values 0..1)
        lines = "\n".join(
            f"{r:.6f} {g:.6f} {b:.6f}" for r, g, b in rgb
        )
        result = subprocess.run(
            [self._xicclu, "-ir", "-pl", "-s1", self._icc],
            input=lines,
            capture_output=True,
            text=True,
            check=True,
        )
        return _parse_xicclu_output(result.stdout, len(rgb))

    def __call__(self, rgb: np.ndarray) -> np.ndarray:
        return self.lab_batch(rgb)


def _parse_xicclu_output(stdout: str, expected: int) -> np.ndarray:
    """Parse ``xicclu`` stdout into an N×3 Lab array."""
    lab_values: list[tuple[float, float, float]] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        # xicclu output format:
        #   "R G B [RGB] -> Lut -> L a b [Lab]"
        # Strip anything after '#'
        line = line.split("#")[0].strip()
        # Extract the output portion after "->" (last arrow)
        if "->" in line:
            # Take everything after the last "->"
            output_part = line.rsplit("->", 1)[-1].strip()
            # Remove bracketed tags like [Lab] or [XYZ]
            output_part = re.sub(r'\[.*?\]', '', output_part).strip()
            parts = output_part.split()
        else:
            parts = line.split()
        if len(parts) >= 3:
            try:
                lab_values.append((float(parts[-3]), float(parts[-2]), float(parts[-1])))
            except ValueError:
                continue
    if len(lab_values) != expected:
        raise RuntimeError(
            f"xicclu returned {len(lab_values)} Lab values, expected {expected}.\n"
            f"Output was:\n{stdout[:500]}"
        )
    return np.array(lab_values, dtype=np.float64)


# ---------------------------------------------------------------------------
# Section 2 – Candidate pool
# ---------------------------------------------------------------------------

@dataclass
class Candidate:
    rgb: tuple[float, float, float]  # 0..1
    source: str
    tags: dict = field(default_factory=dict)


_PERCEPTUAL_ANCHORS: list[tuple[float, float, float]] = [
    # skin tones
    (0.937, 0.796, 0.678),
    (0.847, 0.667, 0.490),
    (0.706, 0.490, 0.329),
    (0.549, 0.337, 0.180),
    (0.376, 0.196, 0.082),
    # foliage greens
    (0.200, 0.400, 0.098),
    (0.302, 0.545, 0.161),
    (0.133, 0.318, 0.043),
    (0.502, 0.659, 0.290),
    # sky blues
    (0.482, 0.694, 0.878),
    (0.200, 0.522, 0.769),
    (0.075, 0.353, 0.600),
    # neutral grays (calibration anchors)
    (0.125, 0.125, 0.125),
    (0.250, 0.250, 0.250),
    (0.375, 0.375, 0.375),
    (0.500, 0.500, 0.500),
    (0.625, 0.625, 0.625),
    (0.750, 0.750, 0.750),
    (0.875, 0.875, 0.875),
    # ink-limit / saturated primaries
    (1.000, 0.000, 0.000),
    (0.000, 1.000, 0.000),
    (0.000, 0.000, 1.000),
    (1.000, 1.000, 0.000),
    (0.000, 1.000, 1.000),
    (1.000, 0.000, 1.000),
    # common print neutrals / memory colors
    (0.804, 0.361, 0.361),  # warm red
    (0.200, 0.400, 0.600),  # steel blue
    (0.400, 0.600, 0.200),  # fresh green
    (0.600, 0.200, 0.400),  # wine
    (0.867, 0.753, 0.341),  # gold/ochre
]


def _halton(n: int, base: int) -> np.ndarray:
    """Generate n Halton sequence values for given base."""
    seq = np.zeros(n, dtype=np.float64)
    for i in range(n):
        f, r = 1.0, 0.0
        idx = i + 1  # 1-indexed
        while idx > 0:
            f /= base
            r += f * (idx % base)
            idx //= base
        seq[i] = r
    return seq


def build_candidate_pool(
    grid: int = 17,
    halton_n: int = 4096,
    neutrals: int = 65,
    edge_steps: int = 33,
    include_anchors: bool = True,
) -> list[Candidate]:
    """Build the ~10 k candidate RGB pool.

    Parameters
    ----------
    grid:
        Side length of the uniform RGB cube grid (``grid**3`` candidates).
    halton_n:
        Number of Halton low-discrepancy RGB samples.
    neutrals:
        Number of gray steps on the neutral axis (plus perturbations).
    edge_steps:
        Steps per edge/axis curve.
    include_anchors:
        Whether to include hard-coded perceptual anchors.

    Returns
    -------
    Deduplicated, stably-sorted list of Candidate objects.
    """
    seen: set[tuple[int, int, int]] = set()
    pool: list[Candidate] = []

    def _add(r: float, g: float, b: float, source: str, tags: dict) -> None:
        r = max(0.0, min(1.0, r))
        g = max(0.0, min(1.0, g))
        b = max(0.0, min(1.0, b))
        key = (round(r * 255), round(g * 255), round(b * 255))
        if key in seen:
            return
        seen.add(key)
        pool.append(Candidate(rgb=(r, g, b), source=source, tags=tags))

    # --- uniform grid ---
    step = 1.0 / (grid - 1) if grid > 1 else 1.0
    for ri in range(grid):
        for gi in range(grid):
            for bi in range(grid):
                _add(ri * step, gi * step, bi * step, "grid", {"kind": "grid"})

    # --- Halton low-discrepancy ---
    hR = _halton(halton_n, 2)
    hG = _halton(halton_n, 3)
    hB = _halton(halton_n, 5)
    for i in range(halton_n):
        _add(hR[i], hG[i], hB[i], "halton", {"kind": "halton"})

    # --- neutral axis with chromatic perturbations ---
    gray_step = 1.0 / (neutrals - 1) if neutrals > 1 else 1.0
    perturbs = [
        (0.0, 0.0, 0.0),
        (+0.01, -0.01, 0.0),
        (-0.01, +0.01, 0.0),
        (0.0, +0.01, -0.01),
        (0.0, -0.01, +0.01),
        (+0.02, 0.0, -0.02),
        (-0.02, 0.0, +0.02),
    ]
    for ni in range(neutrals):
        v = ni * gray_step
        for dr, dg, db in perturbs:
            _add(v + dr, v + dg, v + db, "neutral", {"kind": "neutral"})

    # --- edge/axis curves ---
    axes = [
        ("R", [(t, 0.0, 0.0) for t in np.linspace(0, 1, edge_steps)]),
        ("G", [(0.0, t, 0.0) for t in np.linspace(0, 1, edge_steps)]),
        ("B", [(0.0, 0.0, t) for t in np.linspace(0, 1, edge_steps)]),
        ("C", [(0.0, t, t) for t in np.linspace(0, 1, edge_steps)]),  # cyan
        ("M", [(t, 0.0, t) for t in np.linspace(0, 1, edge_steps)]),  # magenta
        ("Y", [(t, t, 0.0) for t in np.linspace(0, 1, edge_steps)]),  # yellow
        ("KW", [(t, t, t) for t in np.linspace(0, 1, edge_steps)]),   # black→white
        ("RG", [(t, 1 - t, 0.0) for t in np.linspace(0, 1, edge_steps)]),
        ("GB", [(0.0, t, 1 - t) for t in np.linspace(0, 1, edge_steps)]),
        ("BR", [(1 - t, 0.0, t) for t in np.linspace(0, 1, edge_steps)]),
    ]
    for axis_name, pts in axes:
        for r, g, b in pts:
            _add(r, g, b, "edge", {"kind": "edge", "axis": axis_name})

    # --- hue sweeps at multiple luminance levels ---
    luma_levels = [0.1, 0.3, 0.5, 0.7, 0.9]
    hue_count = 24
    chroma_levels = [0.4, 0.7]  # saturation fraction
    for luma in luma_levels:
        for hi in range(hue_count):
            angle = 2 * math.pi * hi / hue_count
            for chroma in chroma_levels:
                # Simple HSV-like construction
                r_hue = 0.5 + 0.5 * math.cos(angle)
                g_hue = 0.5 + 0.5 * math.cos(angle - 2 * math.pi / 3)
                b_hue = 0.5 + 0.5 * math.cos(angle + 2 * math.pi / 3)
                r = luma + chroma * (r_hue - 0.5)
                g = luma + chroma * (g_hue - 0.5)
                b = luma + chroma * (b_hue - 0.5)
                _add(r, g, b, "hue_sweep", {"kind": "hue_sweep", "luma": luma, "chroma": chroma})

    # --- perceptual anchors ---
    if include_anchors:
        for r, g, b in _PERCEPTUAL_ANCHORS:
            _add(r, g, b, "anchor", {"kind": "anchor"})

    # Stable sort for determinism
    pool.sort(key=lambda c: (c.source, c.rgb))
    return pool


# ---------------------------------------------------------------------------
# Section 3 – Pass-1 ingestion
# ---------------------------------------------------------------------------

def load_pass1_lab(
    ti3_path: Path,
    predictor: Callable[[np.ndarray], np.ndarray],
) -> np.ndarray:
    """Return N×3 Lab array for all Pass-1 patches via the predictor.

    RGB values are taken from the Pass-1 .ti3 target values (0..100 scale,
    normalised to 0..1 before passing to the predictor).
    """
    patches = load_measured_patches(ti3_path)
    rgb_01 = np.array(
        [(p.target_rgb[0] / 100.0, p.target_rgb[1] / 100.0, p.target_rgb[2] / 100.0)
         for p in patches],
        dtype=np.float64,
    )
    if len(rgb_01) == 0:
        raise ValueError(f"No patches found in {ti3_path}")
    return predictor(rgb_01)


# ---------------------------------------------------------------------------
# Section 4 – Scoring
# ---------------------------------------------------------------------------

def _delta_e_to_nearest(query: np.ndarray, reference: np.ndarray) -> np.ndarray:
    """Return the ΔE76 from each query point to its nearest reference point.

    Parameters
    ----------
    query:     M×3 Lab array
    reference: N×3 Lab array

    Returns
    -------
    M-length array of minimum ΔE values.
    """
    # Chunked to avoid large M×N arrays in memory
    chunk = 512
    result = np.full(len(query), np.inf, dtype=np.float64)
    for start in range(0, len(reference), chunk):
        ref_chunk = reference[start : start + chunk]  # (c, 3)
        diff = query[:, np.newaxis, :] - ref_chunk[np.newaxis, :, :]  # (M, c, 3)
        dE = np.sqrt(np.sum(diff ** 2, axis=2))  # (M, c)
        result = np.minimum(result, dE.min(axis=1))
    return result


def score_candidates(
    cand_lab: np.ndarray,
    selected_lab: np.ndarray,
    pass1_lab: np.ndarray,
    region_counts: np.ndarray,
    *,
    novelty_soft_cap: float = 30.0,
) -> np.ndarray:
    """Compute the composite score for each candidate.

    Parameters
    ----------
    cand_lab:         N×3 Lab of all (remaining) candidates.
    selected_lab:     S×3 Lab of already-selected patches (may be empty).
    pass1_lab:        M×3 Lab of Pass-1 patches.
    region_counts:    8×8×8 voxel occupancy counts (Pass-1 + selected).
    novelty_soft_cap: Saturation point for the novelty term (ΔE).

    Returns
    -------
    N-length score array.
    """
    # Reference set = pass1 + selected
    if len(selected_lab) > 0:
        reference = np.vstack([pass1_lab, selected_lab])
    else:
        reference = pass1_lab

    # --- novelty (soft-capped) ---
    raw_dE = _delta_e_to_nearest(cand_lab, reference)
    novelty = novelty_soft_cap * np.tanh(raw_dE / novelty_soft_cap)

    # --- region undercoverage ---
    # Map L*a*b* to voxel indices in an 8×8×8 grid
    # L*: 0..100 → 0..7;  a*,b*: -128..127 → 0..7
    L_idx = np.clip((cand_lab[:, 0] / 100.0 * 8).astype(int), 0, 7)
    a_idx = np.clip(((cand_lab[:, 1] + 128) / 256.0 * 8).astype(int), 0, 7)
    b_idx = np.clip(((cand_lab[:, 2] + 128) / 256.0 * 8).astype(int), 0, 7)
    occupancy = region_counts[L_idx, a_idx, b_idx].astype(np.float64)
    max_occ = occupancy.max() if occupancy.max() > 0 else 1.0
    region_undercoverage = 1.0 - (occupancy / max_occ)

    # --- neutrality bonus (chroma-based) ---
    chroma = np.sqrt(cand_lab[:, 1] ** 2 + cand_lab[:, 2] ** 2)
    neutrality_bonus = np.exp(-chroma / 10.0)

    # --- luminance balance bonus ---
    # Computed externally and passed in via region_counts; here we just use the
    # raw L* distribution to bias toward under-represented tertiles.
    # The caller provides region_counts which already encodes luma distribution.
    # Additionally compute a direct luma-tertile bonus:
    L_star = cand_lab[:, 0]
    luma_bonus = np.zeros(len(cand_lab), dtype=np.float64)
    for mask, col_idx in [
        (L_star < 33.0, 0),      # shadow
        ((L_star >= 33.0) & (L_star < 66.0), 1),  # mid
        (L_star >= 66.0, 2),     # highlight
    ]:
        if mask.any():
            luma_bonus[mask] = 1.0  # will be scaled by caller weight

    score = (
        1.0 * novelty
        + 0.5 * region_undercoverage
        + 0.3 * neutrality_bonus
        + 0.3 * luma_bonus
    )
    return score


def _build_region_counts(lab_points: np.ndarray) -> np.ndarray:
    """Build an 8×8×8 voxel count array from a set of Lab points."""
    counts = np.zeros((8, 8, 8), dtype=np.int32)
    if len(lab_points) == 0:
        return counts
    L_idx = np.clip((lab_points[:, 0] / 100.0 * 8).astype(int), 0, 7)
    a_idx = np.clip(((lab_points[:, 1] + 128) / 256.0 * 8).astype(int), 0, 7)
    b_idx = np.clip(((lab_points[:, 2] + 128) / 256.0 * 8).astype(int), 0, 7)
    for l, a, b in zip(L_idx, a_idx, b_idx):
        counts[l, a, b] += 1
    return counts


def _luma_tertile_counts(lab_points: np.ndarray) -> np.ndarray:
    """Return [shadow_count, mid_count, highlight_count]."""
    if len(lab_points) == 0:
        return np.zeros(3, dtype=np.int32)
    L = lab_points[:, 0]
    return np.array([
        (L < 33.0).sum(),
        ((L >= 33.0) & (L < 66.0)).sum(),
        (L >= 66.0).sum(),
    ], dtype=np.int32)


# ---------------------------------------------------------------------------
# Section 5 – Greedy selection
# ---------------------------------------------------------------------------

_FORCED_RGB: list[tuple[float, float, float]] = [
    (1.0, 1.0, 1.0),  # white
    (0.0, 0.0, 0.0),  # black
    (0.25, 0.25, 0.25),
    (0.50, 0.50, 0.50),
    (0.75, 0.75, 0.75),
]


def select_patches(
    candidates: list[Candidate],
    predictor: Callable[[np.ndarray], np.ndarray],
    pass1_lab: np.ndarray,
    target_n: int,
    *,
    min_dE: float = 2.5,
    novelty_soft_cap: float = 30.0,
) -> list[Candidate]:
    """Greedy coverage-maximising patch selection.

    Parameters
    ----------
    candidates:       Full candidate pool (from build_candidate_pool).
    predictor:        Callable np.ndarray(N,3)→Lab(N,3).
    pass1_lab:        M×3 Lab of Pass-1 patches (predicted, consistent space).
    target_n:         Number of patches to select.
    min_dE:           Minimum ΔE76 separation between any two selected patches.
    novelty_soft_cap: Saturation ΔE for the novelty scoring term.

    Returns
    -------
    Ordered list of selected Candidate objects (forced anchors first, then greedy).
    """
    # --- project entire pool to Lab ---
    rgb_arr = np.array([c.rgb for c in candidates], dtype=np.float64)
    cand_lab = predictor(rgb_arr)

    # --- build forced anchor set ---
    forced_rgb_set = {
        (round(r * 255), round(g * 255), round(b * 255))
        for r, g, b in _FORCED_RGB
    }
    forced_indices: list[int] = []
    other_indices: list[int] = []
    for idx, c in enumerate(candidates):
        key = (round(c.rgb[0] * 255), round(c.rgb[1] * 255), round(c.rgb[2] * 255))
        if key in forced_rgb_set:
            forced_indices.append(idx)
        else:
            other_indices.append(idx)

    selected_indices: list[int] = []
    selected_lab_list: list[np.ndarray] = []

    def _accept(idx: int) -> bool:
        """True if candidate at idx is far enough from all already-selected."""
        if not selected_lab_list:
            return True
        sel = np.vstack(selected_lab_list)
        diffs = sel - cand_lab[idx]
        dEs = np.sqrt(np.sum(diffs ** 2, axis=1))
        return bool(dEs.min() >= min_dE)

    # Force anchors first (in stable order)
    for idx in forced_indices:
        if len(selected_indices) >= target_n:
            break
        if _accept(idx):
            selected_indices.append(idx)
            selected_lab_list.append(cand_lab[idx])

    # Greedy selection from the rest
    active = list(other_indices)  # indices still eligible
    # Pre-compute running nearest-ΔE to (pass1 + selected)
    if selected_lab_list:
        ref_so_far = np.vstack([pass1_lab] + selected_lab_list)
    else:
        ref_so_far = pass1_lab
    running_min_dE = _delta_e_to_nearest(cand_lab, ref_so_far)

    while len(selected_indices) < target_n and active:
        # Build region counts from pass1 + selected
        if selected_lab_list:
            coverage_lab = np.vstack([pass1_lab] + selected_lab_list)
        else:
            coverage_lab = pass1_lab
        region_counts = _build_region_counts(coverage_lab)

        # Compute scores for active candidates only
        active_arr = np.array(active, dtype=np.int32)
        act_lab = cand_lab[active_arr]
        act_min_dE = running_min_dE[active_arr]

        # Inline novelty with soft cap
        novelty = novelty_soft_cap * np.tanh(act_min_dE / novelty_soft_cap)

        # Region undercoverage
        L_idx = np.clip((act_lab[:, 0] / 100.0 * 8).astype(int), 0, 7)
        a_idx = np.clip(((act_lab[:, 1] + 128) / 256.0 * 8).astype(int), 0, 7)
        b_idx = np.clip(((act_lab[:, 2] + 128) / 256.0 * 8).astype(int), 0, 7)
        occupancy = region_counts[L_idx, a_idx, b_idx].astype(np.float64)
        max_occ = occupancy.max() if occupancy.max() > 0 else 1.0
        region_uc = 1.0 - (occupancy / max_occ)

        # Neutrality bonus
        chroma = np.sqrt(act_lab[:, 1] ** 2 + act_lab[:, 2] ** 2)
        neutrality = np.exp(-chroma / 10.0)

        # Luma balance bonus: favour the most under-represented tertile
        luma_counts = _luma_tertile_counts(coverage_lab)
        underrep_tertile = int(np.argmin(luma_counts))
        L_star = act_lab[:, 0]
        luma_masks = [L_star < 33.0, (L_star >= 33.0) & (L_star < 66.0), L_star >= 66.0]
        luma_bonus = luma_masks[underrep_tertile].astype(np.float64)

        scores = (
            1.0 * novelty
            + 0.5 * region_uc
            + 0.3 * neutrality
            + 0.3 * luma_bonus
        )

        # Apply min_dE gate — mask out candidates too close to selected
        if selected_lab_list:
            sel_lab = np.vstack(selected_lab_list)
            # Compute ΔE from each active candidate to every selected patch
            diff_to_sel = act_lab[:, np.newaxis, :] - sel_lab[np.newaxis, :, :]
            dE_to_sel = np.sqrt(np.sum(diff_to_sel ** 2, axis=2)).min(axis=1)
            eligible_mask = dE_to_sel >= min_dE
        else:
            eligible_mask = np.ones(len(active), dtype=bool)

        if not eligible_mask.any():
            break  # No eligible candidates left

        scores[~eligible_mask] = -np.inf

        # Tie-break: sort by score desc, then by (source, rgb) lex for determinism
        best_local = int(np.argmax(scores))
        best_global = active[best_local]

        selected_indices.append(best_global)
        new_lab = cand_lab[best_global]
        selected_lab_list.append(new_lab)

        # Update running nearest-ΔE for all remaining active
        diff = cand_lab - new_lab  # (N, 3)
        dE_new = np.sqrt(np.sum(diff ** 2, axis=1))
        running_min_dE = np.minimum(running_min_dE, dE_new)

        active.pop(best_local)

    return [candidates[i] for i in selected_indices]


# ---------------------------------------------------------------------------
# Section 6 – TI1 exporter
# ---------------------------------------------------------------------------

def _lab_to_xyz_d50(lab: np.ndarray) -> np.ndarray:
    """Convert N×3 Lab (D50) to N×3 XYZ (D50, scaled 0–100)."""
    # D50 white point (scaled to Y=100)
    Xn, Yn, Zn = 96.42, 100.0, 82.49

    L = lab[:, 0]
    a = lab[:, 1]
    b = lab[:, 2]

    fy = (L + 16.0) / 116.0
    fx = a / 500.0 + fy
    fz = fy - b / 200.0

    delta = 6.0 / 29.0

    X = np.where(fx > delta, fx ** 3, (fx - 16.0 / 116.0) * 3 * delta ** 2) * Xn
    Y = np.where(fy > delta, fy ** 3, (fy - 16.0 / 116.0) * 3 * delta ** 2) * Yn
    Z = np.where(fz > delta, fz ** 3, (fz - 16.0 / 116.0) * 3 * delta ** 2) * Zn

    return np.column_stack([X, Y, Z])


# Device combination corner RGBs (white, CMY, RGB, black, mid-gray)
_DEVICE_COMBO_RGB: list[tuple[float, float, float]] = [
    (1.0, 1.0, 1.0),
    (0.0, 1.0, 1.0),
    (1.0, 0.0, 1.0),
    (0.0, 0.0, 1.0),
    (1.0, 1.0, 0.0),
    (0.0, 1.0, 0.0),
    (1.0, 0.0, 0.0),
    (0.0, 0.0, 0.0),
    (0.5, 0.5, 0.5),
]


def write_ti1(
    path: Path,
    patches: Sequence[Candidate],
    *,
    predictor: Optional[Callable[[np.ndarray], np.ndarray]] = None,
    descriptor: str = "Argyll Calibration Target chart information 1",
    created: Optional[str] = None,
) -> None:
    """Write a CGATS CTI1 file readable by Argyll ``printtarg``.

    Parameters
    ----------
    path:       Output .ti1 path.
    patches:    Ordered sequence of selected Candidate objects.
    predictor:  Optional RGB→Lab predictor for generating expected XYZ values.
                If provided, the output includes XYZ and the two extra tables
                required by printtarg.
    descriptor: CGATS DESCRIPTOR keyword value.
    created:    Optional CREATED timestamp string; defaults to current UTC time.
    """
    if created is None:
        created = datetime.now(timezone.utc).strftime("%a %b %d %H:%M:%S %Y")

    # Predict XYZ for all patches if predictor available
    xyz_data: Optional[np.ndarray] = None
    if predictor is not None:
        rgb_arr = np.array([c.rgb for c in patches], dtype=np.float64)
        lab_arr = predictor(rgb_arr)
        xyz_data = _lab_to_xyz_d50(lab_arr)

    # --- Table 1: Main patches ---
    lines: list[str] = ["CTI1", ""]
    lines.append(f'DESCRIPTOR "{descriptor}"')
    lines.append('ORIGINATOR "wsprofiler pass2_generator"')
    lines.append(f'CREATED "{created}"')

    if xyz_data is not None:
        # Use D65 white point (targen default for RGB without preconditioning)
        # This matches Argyll's standard: 95.106486 100.0 108.844025
        wp_xyz = np.array([95.106486, 100.0, 108.844025])
        lines.append(f'APPROX_WHITE_POINT "{wp_xyz[0]:.6f} {wp_xyz[1]:.6f} {wp_xyz[2]:.6f}"')
        lines.append('COLOR_REP "iRGB"')
        lines.append('ACCURATE_EXPECTED_VALUES "true"')
    else:
        lines.append('COLOR_REP "RGB"')

    lines.append("")

    if xyz_data is not None:
        lines.append("NUMBER_OF_FIELDS 7")
        lines.append("BEGIN_DATA_FORMAT")
        lines.append("SAMPLE_ID RGB_R RGB_G RGB_B XYZ_X XYZ_Y XYZ_Z")
        lines.append("END_DATA_FORMAT")
        lines.append("")
        lines.append(f"NUMBER_OF_SETS {len(patches)}")
        lines.append("BEGIN_DATA")
        for i, candidate in enumerate(patches, start=1):
            r, g, b = candidate.rgb
            x, y, z = xyz_data[i - 1]
            lines.append(
                f"{i} {r * 100.0:.5f} {g * 100.0:.5f} {b * 100.0:.5f} "
                f"{x:.6f} {y:.6f} {z:.6f}"
            )
    else:
        lines.append("NUMBER_OF_FIELDS 4")
        lines.append("BEGIN_DATA_FORMAT")
        lines.append("SAMPLE_ID RGB_R RGB_G RGB_B")
        lines.append("END_DATA_FORMAT")
        lines.append("")
        lines.append(f"NUMBER_OF_SETS {len(patches)}")
        lines.append("BEGIN_DATA")
        for i, candidate in enumerate(patches, start=1):
            r, g, b = candidate.rgb
            lines.append(f"{i} {r * 100.0:.4f} {g * 100.0:.4f} {b * 100.0:.4f}")

    lines.append("END_DATA")

    # --- Tables 2 & 3: required by printtarg ---
    if predictor is not None:
        # Compute XYZ for density extremes and device combos
        density_rgb = np.array([
            (0.0, 0.5, 0.5), (0.0, 0.5, 0.5),
            (0.0, 0.0, 0.8), (0.0, 0.0, 0.75),
            (0.2, 0.5, 0.0), (0.0, 0.5, 0.13),
            (0.67, 0.0, 0.0), (0.0, 0.0, 0.0),
        ], dtype=np.float64)
        density_lab = predictor(density_rgb)
        density_xyz = _lab_to_xyz_d50(density_lab)

        combo_rgb = np.array(_DEVICE_COMBO_RGB, dtype=np.float64)
        combo_lab = predictor(combo_rgb)
        combo_xyz = _lab_to_xyz_d50(combo_lab)

        # Table 2: Density extreme values
        lines.append("CTI1")
        lines.append("")
        lines.append(f'DESCRIPTOR "{descriptor}"')
        lines.append('ORIGINATOR "wsprofiler pass2_generator"')
        lines.append(f'DENSITY_EXTREME_VALUES "{len(density_rgb)}"')
        lines.append(f'CREATED "{created}"')
        lines.append("")
        lines.append("NUMBER_OF_FIELDS 7")
        lines.append("BEGIN_DATA_FORMAT")
        lines.append("INDEX RGB_R RGB_G RGB_B XYZ_X XYZ_Y XYZ_Z")
        lines.append("END_DATA_FORMAT")
        lines.append("")
        lines.append(f"NUMBER_OF_SETS {len(density_rgb)}")
        lines.append("BEGIN_DATA")
        for i, (rgb, xyz) in enumerate(zip(density_rgb, density_xyz)):
            lines.append(
                f"{i} {rgb[0] * 100:.5f} {rgb[1] * 100:.5f} {rgb[2] * 100:.5f} "
                f"{xyz[0]:.6f} {xyz[1]:.6f} {xyz[2]:.6f}"
            )
        lines.append("END_DATA")

        # Table 3: Device combination values
        lines.append("CTI1")
        lines.append("")
        lines.append(f'DESCRIPTOR "{descriptor}"')
        lines.append('ORIGINATOR "wsprofiler pass2_generator"')
        lines.append(f'DEVICE_COMBINATION_VALUES "{len(combo_rgb)}"')
        lines.append(f'CREATED "{created}"')
        lines.append("")
        lines.append("NUMBER_OF_FIELDS 7")
        lines.append("BEGIN_DATA_FORMAT")
        lines.append("INDEX RGB_R RGB_G RGB_B XYZ_X XYZ_Y XYZ_Z")
        lines.append("END_DATA_FORMAT")
        lines.append("")
        lines.append(f"NUMBER_OF_SETS {len(combo_rgb)}")
        lines.append("BEGIN_DATA")
        for i, (rgb, xyz) in enumerate(zip(combo_rgb, combo_xyz)):
            lines.append(
                f"{i} {rgb[0] * 100:.5f} {rgb[1] * 100:.5f} {rgb[2] * 100:.5f} "
                f"{xyz[0]:.6f} {xyz[1]:.6f} {xyz[2]:.6f}"
            )
        lines.append("END_DATA")

    lines.append("")
    path.write_text("\n".join(lines))


# ---------------------------------------------------------------------------
# Section 7 – Public entry point
# ---------------------------------------------------------------------------

def generate_pass2_ti1(
    precond_icc: Path,
    pass1_ti3: Path,
    out_ti1: Path,
    target_n: int,
    xicclu_path: Path,
    *,
    min_dE: float = 2.5,
    novelty_soft_cap: float = 30.0,
    grid: int = 17,
    halton_n: int = 4096,
    neutrals: int = 65,
    edge_steps: int = 33,
    created: Optional[str] = None,
) -> Path:
    """Generate a Pass-2 .ti1 patch file using coverage-maximising selection.

    This is the single public entry point. It is a pure, deterministic function:
    identical inputs produce identical outputs (modulo the ``created`` timestamp).

    Parameters
    ----------
    precond_icc:      Path to the intermediate ICC profile built from Pass-1.
    pass1_ti3:        Path to Pass-1 .ti3 measurement file.
    out_ti1:          Output path for the generated .ti1 file.
    target_n:         Number of patches to select.
    xicclu_path:      Path to Argyll ``xicclu`` binary.
    min_dE:           Minimum ΔE76 separation between any two selected patches.
    novelty_soft_cap: ΔE value at which the novelty scoring term saturates.
    grid:             Uniform grid side length for the candidate pool.
    halton_n:         Number of Halton samples in the candidate pool.
    neutrals:         Number of neutral axis steps in the candidate pool.
    edge_steps:       Steps per axis/edge curve in the candidate pool.
    created:          Optional timestamp string; pass a fixed value for tests.

    Returns
    -------
    Path to the written .ti1 file.
    """
    predictor = XicclLabPredictor(xicclu_path, precond_icc)

    candidates = build_candidate_pool(
        grid=grid,
        halton_n=halton_n,
        neutrals=neutrals,
        edge_steps=edge_steps,
    )

    pass1_lab = load_pass1_lab(pass1_ti3, predictor)

    selected = select_patches(
        candidates,
        predictor,
        pass1_lab,
        target_n,
        min_dE=min_dE,
        novelty_soft_cap=novelty_soft_cap,
    )

    write_ti1(out_ti1, selected, predictor=predictor, created=created)
    return out_ti1
