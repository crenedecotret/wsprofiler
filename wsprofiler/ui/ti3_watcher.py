"""File watcher for TI3 file updates during chartread measurement."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QFileSystemWatcher, QObject, Signal

from ..ti import ti3


class TI3Watcher(QObject):
    """Watch TI3 file for updates and emit measured colors."""
    
    # Emitted when TI3 file changes with dict mapping SAMPLE_LOC -> (R, G, B)
    colorsUpdated = Signal(dict)
    
    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_file_changed)
        self._ti3_path: Path | None = None
        self._converter: Callable[[tuple[float, float, float]], tuple[int, int, int]] | None = None
    
    def start_watching(self, ti3_path: Path, xyz_to_rgb_converter: Callable[[tuple[float, float, float]], tuple[int, int, int]] | None = None) -> None:
        """Start watching a TI3 file for changes."""
        self.stop_watching()
        self._ti3_path = ti3_path
        self._converter = xyz_to_rgb_converter
        
        # Add to watcher if file exists
        if ti3_path.exists():
            self._watcher.addPath(str(ti3_path))
            # Load initial data
            self._load_and_emit()
    
    def stop_watching(self) -> None:
        """Stop watching the file."""
        if self._ti3_path:
            self._watcher.removePaths(self._watcher.files())
            self._ti3_path = None
    
    def _on_file_changed(self, path: str) -> None:
        """Handle file change event."""
        if self._ti3_path and Path(path) == self._ti3_path:
            self._load_and_emit()
            # Re-add path: atomic rewrites (write-to-temp + rename) remove the
            # old inode from QFileSystemWatcher on Linux.
            if str(self._ti3_path) not in self._watcher.files():
                if self._ti3_path.exists():
                    self._watcher.addPath(str(self._ti3_path))
    
    def _load_and_emit(self) -> None:
        """Load TI3 file and emit measured colors."""
        if not self._ti3_path or not self._ti3_path.exists():
            return
        
        try:
            patches = ti3.load_measured_patches(self._ti3_path)
            color_dict: dict[str, tuple[int, int, int]] = {}
            
            for patch in patches:
                if patch.xyz:
                    if self._converter:
                        rgb = self._converter(patch.xyz)
                    else:
                        # Simple conversion assuming sRGB-like display
                        rgb = _simple_xyz_to_rgb(patch.xyz)
                    color_dict[patch.sample_loc] = rgb
            
            self.colorsUpdated.emit(color_dict)
        except Exception:
            # Ignore parse errors during active writing
            pass


def _simple_xyz_to_rgb(xyz: tuple[float, float, float]) -> tuple[int, int, int]:
    """XYZ to sRGB conversion with chromatic adaptation.
    
    Uses Bradford adaptation from the chart's white point to D65,
    then standard sRGB conversion with improved gamut mapping.
    """
    x, y, z = xyz
    
    # Chart white point from TI3 header (APPROX_WHITE_POINT ~ 87.5, 90.4, 74.9)
    # Normalize to this white point first
    wx, wy, wz = 87.5, 90.4, 74.9
    
    # Bradford chromatic adaptation matrix from chart white to D65
    # D65: (95.047, 100.0, 108.883)
    # This corrects for the different white points
    xn, yn, zn = x / wy * 100.0, y / wy * 100.0, z / wy * 100.0
    
    # Bradford adaptation coefficients (simplified)
    # Convert to LMS cone space, scale, convert back
    l =  0.8951 * xn +  0.2664 * yn + -0.1614 * zn
    m = -0.7502 * xn +  1.7135 * yn +  0.0367 * zn
    s =  0.0389 * xn + -0.0685 * yn +  1.0296 * zn
    
    # Scale to D65 white (LMS of D65 / LMS of chart white)
    l_dst, m_dst, s_dst = 0.95047, 1.0, 1.08883  # D65 in relative terms
    l_src, m_src, s_src = 0.9410, 1.0, 0.8523   # Chart white in LMS
    
    l = l * (l_dst / l_src)
    m = m * (m_dst / m_src)
    s = s * (s_dst / s_src)
    
    # Back to XYZ (D65 adapted)
    xa =  0.98699 * l + -0.14705 * m +  0.15996 * s
    ya =  0.43231 * l +  0.51836 * m +  0.04929 * s
    za = -0.00853 * l +  0.04004 * m +  0.96849 * s
    
    # D65 to sRGB linear (scale by 100 since we normalized to Y=100)
    xa, ya, za = xa / 100.0, ya / 100.0, za / 100.0
    
    # sRGB matrix
    r_lin =  3.2406 * xa - 1.5372 * ya - 0.4986 * za
    g_lin = -0.9689 * xa + 1.8758 * ya + 0.0415 * za
    b_lin =  0.0557 * xa - 0.2040 * ya + 1.0570 * za
    
    # Soft clamp for out-of-gamut colors (preserve hue, reduce saturation)
    def soft_clamp(c: float) -> float:
        if c < 0:
            return c / (1 - c) * 0.5  # Compress negative values
        return min(c, 1.2)  # Allow slight overshoot up to 1.2
    
    r_lin = soft_clamp(r_lin)
    g_lin = soft_clamp(g_lin)
    b_lin = soft_clamp(b_lin)
    
    # sRGB gamma
    def gamma(c: float) -> float:
        c = max(0, min(1, c))
        if c <= 0.0031308:
            return 12.92 * c
        return 1.055 * (c ** (1.0 / 2.4)) - 0.055
    
    r = int(gamma(r_lin) * 255)
    g = int(gamma(g_lin) * 255)
    b = int(gamma(b_lin) * 255)
    
    return (max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)))
