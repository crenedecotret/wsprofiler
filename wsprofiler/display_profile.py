"""Cross-platform discovery of the active display's ICC profile.

On Linux/X11 the primary source is the ``_ICC_PROFILE`` atom on the
root window, read via ``python-xlib`` with a large ``max_length``
(equivalent to ``XGetWindowProperty(…, i32::MAX)``).  This returns the
**complete** profile regardless of size — ``xprop`` truncates its
display, but the X server holds all 1.2+ MB of a LUT-based profile.

Fallbacks:  user-configured file path, filesystem search in standard
directories, and finally Qt's ``QScreen.colorSpace()`` (works on
Windows / macOS; unreliable on X11 with truncated root-window atoms).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QSettings
from PySide6.QtGui import QColorSpace, QGuiApplication


_SETTINGS_KEY_PATH = "display/profile_path"


def get_display_color_space(
    path_override: Optional[Path] = None,
) -> Optional[QColorSpace]:
    """Return the active display's ``QColorSpace``, or *None*.

    *path_override* can be a user-configured path; if omitted the
    value from ``QSettings`` is tried first.
    """
    if path_override is None:
        saved = QSettings().value(_SETTINGS_KEY_PATH)
        if saved:
            path_override = Path(str(saved))

    if path_override is not None and path_override.is_file():
        cs = _load_profile_file(path_override)
        if cs is not None:
            return cs

    if sys.platform == "linux":
        cs = _x11_atom_color_space()
        if cs is not None:
            return cs
        cs = _fs_color_space()
        if cs is not None:
            return cs

    return _qt_color_space()


# ---------------------------------------------------------------------------
# Linux/X11: root-window _ICC_PROFILE atom (primary)
# ---------------------------------------------------------------------------


def _x11_atom_color_space() -> Optional[QColorSpace]:
    """Read ``_ICC_PROFILE`` from the X11 root window.

    Uses ``python-xlib`` to call ``XGetWindowProperty`` with a large
    ``max_length`` so the X server returns the full assembled property
    value — no truncation regardless of profile size.
    """
    try:
        from Xlib import display, X
    except ImportError:
        return None

    try:
        d = display.Display()
        root = d.screen().root
        atom = d.intern_atom("_ICC_PROFILE")
        reply = root.get_property(atom, X.AnyPropertyType, 0, 10_000_000)
        d.close()

        if reply.value is None or len(reply.value) < 128:
            return None

        raw = bytes(reply.value)
        cs = QColorSpace.fromIccProfile(raw)
        if cs.isValid():
            return cs
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# File-system fallback
# ---------------------------------------------------------------------------


def _fs_color_space() -> Optional[QColorSpace]:
    """Search well-known directories for a display ICC profile.

    Scans user home directories first (where ``dispwin -I`` stores
    profiles), then system paths.  Within each directory, candidates
    are sorted by file size (descending) so that large LUT-based
    display profiles are preferred over small generic reference files.
    """
    home_dirs = [
        Path.home() / ".local" / "share" / "icc",
        Path.home() / ".color" / "icc",
    ]
    system_dirs = [
        Path("/usr/share/color/icc"),
        Path("/usr/share/color/icc/colord"),
        Path("/usr/local/share/color/icc"),
    ]

    for directory in home_dirs + system_dirs:
        if not directory.is_dir():
            continue
        candidates: list[Path] = []
        for ext in ("*.icc", "*.icm"):
            candidates.extend(directory.glob(ext))
        candidates.sort(key=lambda p: p.stat().st_size, reverse=True)
        for candidate in candidates:
            cs = _load_profile_file(candidate)
            if cs is not None:
                return cs
    return None


# ---------------------------------------------------------------------------
# Qt fallback
# ---------------------------------------------------------------------------


def _qt_color_space() -> Optional[QColorSpace]:
    """Use Qt's built-in ``QScreen.colorSpace()``."""
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return None
    cs = screen.colorSpace()
    if cs.isValid():
        return cs
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_profile_file(path: Path) -> Optional[QColorSpace]:
    """Load an ICC/ICM file and return a QColorSpace, or *None*."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) < 128:
        return None
    cs = QColorSpace.fromIccProfile(data)
    if cs.isValid():
        return cs
    return None


# ---------------------------------------------------------------------------
# sRGB → display transform (cached, used by rendering code)
# ---------------------------------------------------------------------------

_xf_cache: Optional["QColorTransform"] = None  # noqa: F821
_resolved: bool = False  # first-lookup guard


def apply_display_profile(r: int, g: int, b: int) -> tuple[int, int, int]:
    """Transform sRGB patch colours through the display ICC profile.

    The ``QColorSpace`` and ``QColorTransform`` are resolved once on
    the first call and cached for the lifetime of the process.  If no
    display profile is available, the values are returned unchanged
    (sRGB fallback).
    """
    global _xf_cache, _resolved

    from PySide6.QtGui import QColor, QColorSpace  # defer import

    if not _resolved:
        _resolved = True
        cs = get_display_color_space()
        if cs is not None:
            srgb = QColorSpace(QColorSpace.NamedColorSpace.SRgb)
            _xf_cache = srgb.transformationToColorSpace(cs)

    if _xf_cache is None:
        return r, g, b

    c_out = _xf_cache.map(QColor(r, g, b))
    nr = max(0, min(255, c_out.red()))
    ng = max(0, min(255, c_out.green()))
    nb = max(0, min(255, c_out.blue()))
    return nr, ng, nb
