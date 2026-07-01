"""Regression tests for MeasurementPage completion signalling.

Covers BUG 2: on a fresh chartread run, ``measurementsComplete`` must be
emitted exactly once when chartread exits 0 with a ``.ti3`` on disk,
even when the ti3 parser fails on the freshly-written file.
"""
from __future__ import annotations

import os

# Force offscreen Qt platform BEFORE any PySide6 import.
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from pathlib import Path

from PySide6.QtWidgets import QApplication


def _ensure_app():
    return QApplication.instance() or QApplication([])


def test_on_finished_emits_measurements_complete_once(tmp_path: Path, qapp):
    from wsprofiler.ui.pages.measurement_page import MeasurementPage

    page = MeasurementPage(workspace=tmp_path)
    # Simulate a chart having been loaded: the chart stem determines where
    # chartread writes its .ti3.
    chart_path = tmp_path / "mychart.ti2"
    chart_path.write_text("dummy ti2")
    page._current_chart_path = chart_path
    # Simulate chartread writing the .ti3 and exiting cleanly.
    ti3_path = chart_path.with_suffix(".ti3")
    ti3_path.write_text("dummy ti3 (unparseable)")

    emitted: list[Path] = []
    page.measurementsComplete.connect(lambda p: emitted.append(p))
    # Also connect measurementStopped so we can confirm the full exit path.
    stopped = []
    page.measurementStopped.connect(lambda: stopped.append(True))

    page._on_finished(0)

    # Exactly one completion signal, carrying the ti3 path.
    assert len(emitted) == 1, f"expected 1 completion emit, got {len(emitted)}"
    assert emitted[0] == ti3_path
    # The page should have recorded the ti3 path as the current one.
    assert page._current_ti3_path == ti3_path
    # measurementStopped still fires (it is independent of completion).
    assert stopped == [True]


def test_on_finished_nonzero_exit_does_not_emit_complete(tmp_path: Path, qapp):
    from wsprofiler.ui.pages.measurement_page import MeasurementPage

    page = MeasurementPage(workspace=tmp_path)
    chart_path = tmp_path / "mychart.ti2"
    chart_path.write_text("dummy ti2")
    page._current_chart_path = chart_path
    ti3_path = chart_path.with_suffix(".ti3")
    ti3_path.write_text("dummy ti3")

    emitted = []
    page.measurementsComplete.connect(lambda p: emitted.append(p))
    stopped = []
    page.measurementStopped.connect(lambda: stopped.append(True))

    page._on_finished(1)  # non-zero: chartread failed

    assert emitted == []
    # measurementStopped still fires so the wizard can save partial state.
    assert stopped == [True]


def test_load_existing_ti3_default_emits_complete(tmp_path: Path, qapp):
    """The resume path (default emit_complete=True) still signals completion."""
    from wsprofiler.ui.pages.measurement_page import MeasurementPage

    page = MeasurementPage(workspace=tmp_path)
    # Build a minimal valid ti3 so the parser yields at least one XYZ patch.
    ti3_path = tmp_path / "chart.ti3"
    ti3_path.write_text(
        'CTI3\n'
        'DESCRIPTOR "Argyll Calibration Target chart information 3"\n'
        'KEYWORD "SAMPLE_LOC"\n'
        'KEYWORD "XYZ"\n'
        'NUMBER_OF_SETS 1\n'
        'BEGIN_DATA_FORMAT\n'
        'SAMPLE_ID SAMPLE_LOC XYZ_X XYZ_Y XYZ_Z\n'
        'END_DATA_FORMAT\n'
        'BEGIN_DATA\n'
        '1 A1 87.5 90.4 74.9\n'
        'END_DATA\n'
    )

    emitted = []
    page.measurementsComplete.connect(lambda p: emitted.append(p))
    page._load_existing_ti3(ti3_path, emit_complete=True)
    assert len(emitted) == 1
    assert emitted[0] == ti3_path


def test_load_existing_ti3_emit_false_does_not_emit(tmp_path: Path, qapp):
    """emit_complete=False suppresses the side-effect emit (used by _on_finished)."""
    from wsprofiler.ui.pages.measurement_page import MeasurementPage

    page = MeasurementPage(workspace=tmp_path)
    ti3_path = tmp_path / "chart.ti3"
    ti3_path.write_text(
        'CTI3\n'
        'DESCRIPTOR "Argyll Calibration Target chart information 3"\n'
        'KEYWORD "SAMPLE_LOC"\n'
        'KEYWORD "XYZ"\n'
        'NUMBER_OF_SETS 1\n'
        'BEGIN_DATA_FORMAT\n'
        'SAMPLE_ID SAMPLE_LOC XYZ_X XYZ_Y XYZ_Z\n'
        'END_DATA_FORMAT\n'
        'BEGIN_DATA\n'
        '1 A1 87.5 90.4 74.9\n'
        'END_DATA\n'
    )

    emitted = []
    page.measurementsComplete.connect(lambda p: emitted.append(p))
    page._load_existing_ti3(ti3_path, emit_complete=False)
    assert emitted == []