"""Tests for TI3 combiner."""

from pathlib import Path

from wsprofiler.ti import ti3_combiner


SAMPLE_TI3 = """CGATS.17

NUMBER_OF_FIELDS 7
BEGIN_DATA_FORMAT
SAMPLE_ID RGB_R RGB_G RGB_B XYZ_X XYZ_Y XYZ_Z
END_DATA_FORMAT

NUMBER_OF_SETS 2
BEGIN_DATA
1 100.00 0.00 0.00 41.24 21.26 1.93
2 0.00 100.00 0.00 35.76 71.52 11.92
END_DATA
"""

SAMPLE_TI3_B = """CGATS.17

NUMBER_OF_FIELDS 7
BEGIN_DATA_FORMAT
SAMPLE_ID RGB_R RGB_G RGB_B XYZ_X XYZ_Y XYZ_Z
END_DATA_FORMAT

NUMBER_OF_SETS 2
BEGIN_DATA
3 0.00 0.00 100.00 18.05 7.22 95.05
4 50.00 50.00 50.00 20.33 21.47 19.33
END_DATA
"""

SAMPLE_TI3_C = """CGATS.17

NUMBER_OF_FIELDS 7
BEGIN_DATA_FORMAT
SAMPLE_ID RGB_R RGB_G RGB_B XYZ_X XYZ_Y XYZ_Z
END_DATA_FORMAT

NUMBER_OF_SETS 1
BEGIN_DATA
5 25.00 25.00 25.00 10.00 10.00 10.00
END_DATA
"""


def test_combine_two_files(tmp_path: Path):
    a = tmp_path / "a.ti3"
    b = tmp_path / "b.ti3"
    out = tmp_path / "out.ti3"
    a.write_text(SAMPLE_TI3)
    b.write_text(SAMPLE_TI3_B)

    ti3_combiner.combine(a, b, out)
    text = out.read_text()

    assert "NUMBER_OF_SETS 4" in text
    assert "1\t100.00\t0.00\t0.00" in text
    assert "2\t0.00\t100.00\t0.00" in text
    assert "3\t0.00\t0.00\t100.00" in text
    assert "4\t50.00\t50.00\t50.00" in text


def test_combine_all_three_files(tmp_path: Path):
    a = tmp_path / "a.ti3"
    b = tmp_path / "b.ti3"
    c = tmp_path / "c.ti3"
    out = tmp_path / "out.ti3"
    a.write_text(SAMPLE_TI3)
    b.write_text(SAMPLE_TI3_B)
    c.write_text(SAMPLE_TI3_C)

    ti3_combiner.combine_all([a, b, c], out)
    text = out.read_text()

    assert "NUMBER_OF_SETS 5" in text
    assert "1\t100.00\t0.00\t0.00" in text
    assert "3\t0.00\t0.00\t100.00" in text
    assert "5\t25.00\t25.00\t25.00" in text


def test_combine_all_single_file(tmp_path: Path):
    a = tmp_path / "a.ti3"
    out = tmp_path / "out.ti3"
    a.write_text(SAMPLE_TI3)

    ti3_combiner.combine_all([a], out)
    text = out.read_text()

    assert "NUMBER_OF_SETS 2" in text


def test_combine_all_empty_list_raises():
    import pytest
    with pytest.raises(ValueError, match="At least one source file"):
        ti3_combiner.combine_all([], Path("out.ti3"))


def test_combine_mismatched_format_raises(tmp_path: Path):
    a = tmp_path / "a.ti3"
    b = tmp_path / "b.ti3"
    out = tmp_path / "out.ti3"
    a.write_text(SAMPLE_TI3)
    b.write_text(
        SAMPLE_TI3_B.replace(
            "SAMPLE_ID RGB_R RGB_G RGB_B XYZ_X XYZ_Y XYZ_Z",
            "SAMPLE_ID RGB_R RGB_G RGB_B LAB_L LAB_A LAB_B"
        )
    )

    import pytest
    with pytest.raises(ValueError, match="Data format mismatch"):
        ti3_combiner.combine_all([a, b], out)
