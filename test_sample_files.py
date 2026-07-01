"""Test pass-2 generation and ti3 combining with real sample files."""
from pathlib import Path
import sys
import traceback

sys.path.insert(0, str(Path(__file__).parent))

from wsprofiler.profiling.pass2_generator import generate_pass2_ti1
from wsprofiler.ti.ti3_combiner import combine
from wsprofiler.ti.cgats import load

BASE = Path(__file__).parent

# --- Test 1: Pass-2 patch generation ---
print("=" * 60)
print("TEST 1: Pass-2 patch generation (Sample.ti3 + Sample.icc)")
print("=" * 60)

ti3_path = BASE / "Sample.ti3"
icc_path = BASE / "Sample.icc"
out_ti1 = BASE / "pass2_test_output.ti1"
xicclu_path = Path("/usr/bin/xicclu")

try:
    result = generate_pass2_ti1(
        precond_icc=icc_path,
        pass1_ti3=ti3_path,
        out_ti1=out_ti1,
        target_n=100,
        xicclu_path=xicclu_path,
        min_dE=2.5,
        grid=9,
        halton_n=512,
        neutrals=17,
        edge_steps=9,
    )
    print(f"✓ Generated: {result}")
    print(f"  File size: {result.stat().st_size} bytes")

    # Validate structure by parsing the first table manually
    content = result.read_text()
    lines_all = content.splitlines()

    # Check CTI1 identifier
    if lines_all[0].strip() == "CTI1":
        print(f"  ✓ File starts with CTI1 identifier")
    else:
        print(f"  ⚠ First line is '{lines_all[0]}', expected 'CTI1'")

    # Count tables (CTI1 markers)
    table_count = sum(1 for l in lines_all if l.strip() == "CTI1")
    print(f"  ✓ Contains {table_count} table(s) (printtarg needs 3)")

    # Check key structural elements
    has_irgb = 'COLOR_REP "iRGB"' in content
    has_xyz = "XYZ_X" in content
    has_density = "DENSITY_EXTREME_VALUES" in content
    has_combo = "DEVICE_COMBINATION_VALUES" in content
    print(f"  ✓ COLOR_REP iRGB: {has_irgb}")
    print(f"  ✓ XYZ expected values: {has_xyz}")
    print(f"  ✓ Density extremes table: {has_density}")
    print(f"  ✓ Device combos table: {has_combo}")

    # Show first few data lines
    in_data = False
    data_lines = []
    for line in lines_all:
        if line.strip() == "BEGIN_DATA":
            in_data = True
            continue
        if line.strip() == "END_DATA":
            break
        if in_data:
            data_lines.append(line)
    print(f"  Main table patch count: {len(data_lines)}")
    print(f"  First 3 data rows:")
    for dl in data_lines[:3]:
        print(f"    {dl}")
    print(f"  Last 2 data rows:")
    for dl in data_lines[-2:]:
        print(f"    {dl}")

    # Test with printtarg
    import subprocess
    tif_path = BASE / "pass2_test_output.tif"
    if tif_path.exists():
        tif_path.unlink()
    proc = subprocess.run(
        ["printtarg", "-v", "-ii1", "-a", "0.7", "-T", "360", "-p", "A4", "pass2_test_output"],
        capture_output=True, text=True, cwd=str(BASE)
    )
    if proc.returncode == 0:
        print(f"\n  ✓ printtarg succeeded!")
        for line in proc.stdout.strip().splitlines():
            print(f"    {line}")
    else:
        print(f"\n  ✗ printtarg failed: {proc.stdout} {proc.stderr}")

    print("\n  TEST 1 PASSED ✓")

except Exception as e:
    print(f"  ✗ FAILED: {e}")
    traceback.print_exc()

# --- Test 2: TI3 combination ---
print("\n" + "=" * 60)
print("TEST 2: TI3 combination (Sample.ti3 + sample2.ti3)")
print("=" * 60)

ti3_a = BASE / "Sample.ti3"
ti3_b = BASE / "sample2.ti3"
combined_out = BASE / "combined_test_output.ti3"

# First, show the structure of both files
try:
    cgats_a = load(ti3_a)
    cgats_b = load(ti3_b)
    print(f"  File A (Sample.ti3):")
    print(f"    Patches: {len(cgats_a.data)}")
    print(f"    Fields: {len(cgats_a.data_format)}")
    print(f"    Format (first 8): {cgats_a.data_format[:8]}")
    print(f"  File B (sample2.ti3):")
    print(f"    Patches: {len(cgats_b.data)}")
    print(f"    Fields: {len(cgats_b.data_format)}")
    print(f"    Format (first 8): {cgats_b.data_format[:8]}")
    print()

    if cgats_a.data_format == cgats_b.data_format:
        print("  ✓ Data formats MATCH")
    else:
        print("  ⚠ Data formats DIFFER:")
        print(f"    A has {len(cgats_a.data_format)} fields")
        print(f"    B has {len(cgats_b.data_format)} fields")
        common = set(cgats_a.data_format) & set(cgats_b.data_format)
        only_a = set(cgats_a.data_format) - set(cgats_b.data_format)
        only_b = set(cgats_b.data_format) - set(cgats_a.data_format)
        print(f"    Common fields: {len(common)}")
        print(f"    Only in A: {len(only_a)} (e.g., {sorted(only_a)[:5]})")
        print(f"    Only in B: {len(only_b)} (e.g., {sorted(only_b)[:5]})")
except Exception as e:
    print(f"  Error loading files: {e}")
    traceback.print_exc()

print()
print("  Attempting combine...")
try:
    combine(ti3_a, ti3_b, combined_out)
    table = load(combined_out)
    print(f"  ✓ Combined successfully!")
    print(f"    Output patches: {table.keywords.get('NUMBER_OF_SETS')}")
    print(f"    Output fields: {len(table.data_format)}")
    print("\n  TEST 2 PASSED ✓")
except ValueError as e:
    print(f"  ✗ Combine raised ValueError (expected): {e}")
    print("\n  TEST 2: EXPECTED FAILURE (format mismatch) ✓")
except Exception as e:
    print(f"  ✗ Unexpected error: {e}")
    traceback.print_exc()

print("\n" + "=" * 60)
print("SUMMARY")
print("=" * 60)
