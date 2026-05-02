from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..platform import is_windows


@dataclass(slots=True)
class ArgyllInstall:
    chartread: Path
    targen: Path
    printtarg: Path
    colprof: Path


def _get_exe_name(name: str) -> str:
    """Get executable name with proper extension for current platform."""
    if is_windows():
        return f"{name}.exe"
    return name


def discover(explicit: Optional[Path] = None) -> ArgyllInstall | None:
    """Discover ArgyllCMS installation on the system.

    Searches for ArgyllCMS binaries in the provided explicit path or on PATH.
    On Windows, looks for .exe extensions; on Unix, looks for plain binary names.
    """
    candidates = []
    if explicit:
        candidates.append(Path(explicit))

    # Try to find chartread on PATH with appropriate extension
    chartread_name = _get_exe_name("chartread")
    candidates.append(Path(shutil.which(chartread_name) or ""))

    for base in candidates:
        if not base:
            continue
        if base.is_file():
            base = base.parent

        # Look for binaries with appropriate extensions
        chartread = base / _get_exe_name("chartread")
        targen = base / _get_exe_name("targen")
        printtarg = base / _get_exe_name("printtarg")
        colprof = base / _get_exe_name("colprof")

        if all(exe.exists() for exe in (chartread, targen, printtarg, colprof)):
            return ArgyllInstall(chartread=chartread, targen=targen, printtarg=printtarg, colprof=colprof)
    return None
