"""Parse ArgyllCMS .ti3 (CGATS) files to extract measured patch data."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True, slots=True)
class MeasuredPatch:
    """A patch with both target and measured values."""
    sample_id: str
    sample_loc: str  # e.g., "G20"
    strip: int  # Parsed from sample_loc
    position: int  # Parsed from sample_loc
    # Original/target RGB (0-100 scale)
    target_rgb: tuple[float, float, float]
    # Measured XYZ
    xyz: tuple[float, float, float] | None
    # Spectral data (if available)
    spectral: list[float] | None = None


def _parse_sample_loc(loc: str) -> tuple[int, int]:
    """Parse SAMPLE_LOC like 'G20' into (strip, position).
    
    Strip is letter-based: A=1, B=2, ..., Z=26, AA=27, etc.
    Position is the number suffix.
    """
    match = re.match(r"([A-Z]+)(\d+)", loc.upper())
    if not match:
        return (0, 0)
    
    letters, number = match.groups()
    # Convert letters to strip number (A=1, B=2, AA=27, etc.)
    strip = 0
    for ch in letters:
        strip = strip * 26 + (ord(ch) - ord('A') + 1)
    
    position = int(number)
    return (strip, position)


def load_measured_patches(path: Path) -> Sequence[MeasuredPatch]:
    """Load patches from a .ti3 file."""
    patches: list[MeasuredPatch] = []
    
    with open(path, "r") as f:
        lines = f.readlines()
    
    # Parse header to find field positions
    field_indices: dict[str, int] = {}
    data_started = False
    
    for line in lines:
        line = line.strip()
        
        if line == "BEGIN_DATA_FORMAT":
            continue
        if line == "END_DATA_FORMAT":
            continue
        if line == "BEGIN_DATA":
            data_started = True
            continue
        if line == "END_DATA":
            break
            
        if not data_started:
            # Parse field names from DATA_FORMAT section
            if "SAMPLE_ID" in line or "SAMPLE_LOC" in line or "RGB_R" in line:
                fields = line.split()
                field_indices = {f: i for i, f in enumerate(fields)}
        else:
            # Parse data line
            parts = line.split()
            if len(parts) < 3:
                continue
                
            # Get required fields
            sample_id = parts[field_indices.get("SAMPLE_ID", 0)]
            sample_loc = parts[field_indices.get("SAMPLE_LOC", 1)].strip('"')
            strip, position = _parse_sample_loc(sample_loc)
            
            # Target RGB
            r_idx = field_indices.get("RGB_R")
            g_idx = field_indices.get("RGB_G")
            b_idx = field_indices.get("RGB_B")
            
            if r_idx is not None and g_idx is not None and b_idx is not None:
                target_rgb = (
                    float(parts[r_idx]),
                    float(parts[g_idx]),
                    float(parts[b_idx])
                )
            else:
                target_rgb = (0.0, 0.0, 0.0)
            
            # Measured XYZ
            x_idx = field_indices.get("XYZ_X")
            y_idx = field_indices.get("XYZ_Y")
            z_idx = field_indices.get("XYZ_Z")
            
            if x_idx is not None and y_idx is not None and z_idx is not None:
                try:
                    xyz = (
                        float(parts[x_idx]),
                        float(parts[y_idx]),
                        float(parts[z_idx])
                    )
                except (ValueError, IndexError):
                    xyz = None
            else:
                xyz = None
            
            patches.append(MeasuredPatch(
                sample_id=sample_id,
                sample_loc=sample_loc,
                strip=strip,
                position=position,
                target_rgb=target_rgb,
                xyz=xyz
            ))
    
    return patches
