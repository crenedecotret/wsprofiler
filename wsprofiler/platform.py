from __future__ import annotations

import sys


def is_windows() -> bool:
    """Check if running on Windows."""
    return sys.platform.startswith("win")


def is_linux() -> bool:
    """Check if running on Linux."""
    return sys.platform.startswith("linux")


def is_macos() -> bool:
    """Check if running on macOS."""
    return sys.platform == "darwin"
