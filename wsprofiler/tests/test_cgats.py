"""Tests for CGATS and ti2 parsing."""

from pathlib import Path

from wsprofiler.ti import cgats, ti2


def test_load_sample_ti2():
    sample = Path(__file__).parent.parent.parent / "assets" / "sample" / "sample_chart.ti2"
    assert sample.exists(), f"Sample file not found: {sample}"
    
    patches = ti2.load_patches(sample)
    assert len(patches) == 572
    
    # Check first patch - SAMPLE_LOC "R22" -> strip=R (18), position=22
    p0 = patches[0]
    assert p0.sample_id == "1"
    assert p0.sample_loc == "R22"
    assert p0.page == 1
    assert p0.strip == 18  # R = 18th letter
    assert p0.position == 22
    assert "RGB_R" in p0.device_values
    
    # Check RGB values are parsed correctly (white in file)
    assert p0.device_values["RGB_R"] == 100.0
    assert p0.device_values["RGB_G"] == 100.0
    assert p0.device_values["RGB_B"] == 100.0
    
    # Check approx_rgb works (white -> 255,255,255)
    rgb = p0.approx_rgb()
    assert rgb == (255, 255, 255)


def test_cgats_load():
    sample = Path(__file__).parent.parent.parent / "assets" / "sample" / "sample_chart.ti2"
    table = cgats.load(sample)
    
    assert "NUMBER_OF_FIELDS" in table.keywords
    assert len(table.data_format) == 8
    assert len(table.data) == 572
    
    # Check data format includes expected fields
    df_upper = [f.upper() for f in table.data_format]
    assert "SAMPLE_ID" in df_upper
    assert "RGB_R" in df_upper


def test_group_by_page():
    sample = Path(__file__).parent.parent.parent / "assets" / "sample" / "sample_chart.ti2"
    patches = ti2.load_patches(sample)
    pages = ti2.group_by_page(patches)
    
    assert 1 in pages
    assert len(pages[1]) == 572
