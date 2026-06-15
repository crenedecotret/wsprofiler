"""ICC profile discovery helpers."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _is_icc_v2(profile_path: Path) -> bool:
    """Check if ICC profile is version 2.x by reading header."""
    try:
        with open(profile_path, "rb") as f:
            header = f.read(24)
        if len(header) < 24:
            return False
        version = int.from_bytes(header[8:12], "big")
        return 0x02000000 <= version < 0x03000000
    except Exception:
        return False


def _find_argyll_ref_dir(colprof_path: Path | None) -> Path | None:
    """Find ArgyllCMS reference profiles directory."""
    if colprof_path is None:
        return None
    ref = colprof_path.parent / "ref"
    if ref.is_dir():
        return ref
    return None


def find_srgb_profile(prefer_v2: bool = True, argyll_ref_dir: Path | None = None) -> Optional[Path]:
    """Find sRGB profile, optionally preferring ICC v2 and Argyll's ref dir."""
    candidates = [
        "sRGB.icm", "sRGB.icc", "sRGB1996.icm", "sRGB1996.icc",
        "sRGB_IEC61966-2-1.icm", "sRGB_IEC61966-2-1.icc",
    ]

    # Search Argyll ref dir first if provided
    if argyll_ref_dir:
        for name in candidates:
            profile_path = argyll_ref_dir / name
            if profile_path.exists():
                if not prefer_v2 or _is_icc_v2(profile_path):
                    return profile_path

    # Search system paths
    search_paths = [
        Path("/usr/share/color/icc"),
        Path("/usr/share/color/icc/colord"),
        Path("/usr/local/share/color/icc"),
        Path("/usr/share/icc-profiles"),
        Path("/usr/share/color-profiles"),
        Path.home() / ".local" / "share" / "icc",
        Path.home() / ".local" / "share" / "color" / "icc",
        Path.home() / ".color" / "icc",
        Path("/usr/share/color"),
        Path("/usr/local/share/color"),
    ]
    for base in search_paths:
        for name in candidates:
            profile_path = base / name
            if profile_path.exists():
                if not prefer_v2 or _is_icc_v2(profile_path):
                    return profile_path
    return None


def find_adobe_rgb_profile(icc_dir: Path | None = None) -> Optional[Path]:
    """Find Adobe RGB (1998) profile."""
    candidates = [
        "AdobeRGB1998.icc", "AdobeRGB1998.icm",
        "Adobe RGB (1998).icc", "Adobe RGB (1998).icm",
    ]

    if icc_dir and icc_dir.is_dir():
        for name in candidates:
            p = icc_dir / name
            if p.exists():
                return p

    search_paths = [
        Path("/usr/share/color/icc"),
        Path("/usr/share/color/icc/colord"),
        Path("/usr/local/share/color/icc"),
        Path.home() / ".local" / "share" / "color" / "icc",
        Path.home() / ".color" / "icc",
    ]
    for base in search_paths:
        for name in candidates:
            p = base / name
            if p.exists():
                return p
    return None


def find_clay_rgb(colprof_path: Path | None = None) -> Optional[str]:
    """Find ClayRGB1998 profile (Argyll's reference wide-gamut RGB)."""
    ref_dir = _find_argyll_ref_dir(colprof_path)
    if ref_dir:
        clay = ref_dir / "ClayRGB1998.icm"
        if clay.exists():
            return str(clay)
    adobe = find_adobe_rgb_profile()
    if adobe:
        return str(adobe)
    srgb = find_srgb_profile(prefer_v2=True, argyll_ref_dir=ref_dir)
    if srgb:
        return str(srgb)
    return None
