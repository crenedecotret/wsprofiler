"""Profile page: wraps ArgyllCMS colprof.

Options used:
    -v      verbose
    -qh     high quality profile
    -S      source colorspace profile for perceptual gamut mapping
"""
from __future__ import annotations

import os
import shlex
from pathlib import Path

from PySide6.QtCore import QProcess, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from ...argyll import discover
from ..log_console import LogConsole


def _find_argyll_ref_dir(colprof_path: Path | None) -> Path | None:
    """Locate ArgyllCMS reference profiles directory."""
    candidates = []
    if colprof_path:
        colprof_dir = colprof_path.parent
        candidates.extend(
            [
                colprof_dir.parent / "share" / "color" / "argyll" / "ref",
                colprof_dir.parent / "lib" / "color" / "argyll" / "ref",
            ]
        )
    candidates.extend(
        [
            Path("/usr/share/color/argyll/ref"),
            Path("/usr/local/share/color/argyll/ref"),
        ]
    )

    for candidate in candidates:
        if candidate.is_dir() and any(candidate.iterdir()):
            return candidate
    return None


def _find_adobe_rgb_profile() -> Path | None:
    """Search system for AdobeRGB or AdobeRGB1998 profiles."""
    candidates = [
        "AdobeRGB1998.icm",
        "AdobeRGB1998.icc",
        "AdobeRGB.icm",
        "AdobeRGB.icc",
    ]
    # Standard ICC profile locations on Linux
    search_paths = [
        # System-wide color directories
        Path("/usr/share/color/icc"),
        Path("/usr/share/color/icc/colord"),
        Path("/usr/local/share/color/icc"),
        Path("/opt/color/icc"),
        # Distribution-specific packages
        Path("/usr/share/icc-profiles"),
        Path("/usr/share/color-profiles"),
        # User profiles
        Path.home() / ".local" / "share" / "icc",
        Path.home() / ".local" / "share" / "color" / "icc",
        Path.home() / ".color" / "icc",
        # Legacy/common locations
        Path("/usr/share/color"),
        Path("/usr/local/share/color"),
        Path("/opt"),
    ]

    if os.name == "nt":
        # Windows system and user profiles
        sys_root = os.environ.get("SystemRoot", "C:\\Windows")
        search_paths.extend([
            Path(sys_root) / "System32" / "spool" / "drivers" / "color",
            Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Color Profiles",
        ])
    
    # First check specific profile files in known locations
    for base in search_paths:
        for name in candidates:
            profile_path = base / name
            if profile_path.exists():
                return profile_path
    
    # Then do recursive search in directories
    for base in search_paths:
        if not base.exists() or not base.is_dir():
            continue
        for root, _, files in os.walk(base):
            for f in files:
                if f.lower() in [c.lower() for c in candidates]:
                    return Path(root) / f
    return None


def _find_srgb_profile() -> Path | None:
    """Search system for sRGB profiles."""
    candidates = [
        "sRGB.icm",
        "sRGB.icc",
        "sRGB1996.icm",
        "sRGB1996.icc",
        "sRGB_IEC61966-2-1.icm",
        "sRGB_IEC61966-2-1.icc",
    ]
    # Standard ICC profile locations on Linux
    search_paths = [
        # System-wide color directories
        Path("/usr/share/color/icc"),
        Path("/usr/share/color/icc/colord"),
        Path("/usr/local/share/color/icc"),
        Path("/opt/color/icc"),
        # Distribution-specific packages
        Path("/usr/share/icc-profiles"),
        Path("/usr/share/color-profiles"),
        # User profiles
        Path.home() / ".local" / "share" / "icc",
        Path.home() / ".local" / "share" / "color" / "icc",
        Path.home() / ".color" / "icc",
        # Legacy/common locations
        Path("/usr/share/color"),
        Path("/usr/local/share/color"),
        Path("/opt"),
    ]

    if os.name == "nt":
        # Windows system and user profiles
        sys_root = os.environ.get("SystemRoot", "C:\\Windows")
        search_paths.extend([
            Path(sys_root) / "System32" / "spool" / "drivers" / "color",
            Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Color Profiles",
        ])
    
    # First check specific profile files in known locations
    for base in search_paths:
        for name in candidates:
            profile_path = base / name
            if profile_path.exists():
                return profile_path
    
    # Then do recursive search in directories
    for base in search_paths:
        if not base.exists() or not base.is_dir():
            continue
        for root, _, files in os.walk(base):
            for f in files:
                if f.lower() in [c.lower() for c in candidates]:
                    return Path(root) / f
    return None


def _find_default_ref_profile(ref_dir: Path | None) -> Path | None:
    """Return ClayRGB1998.icm or first available .icm/.icc in ref dir."""
    if not ref_dir:
        return None
    clay = ref_dir / "ClayRGB1998.icm"
    if clay.exists():
        return clay
    for ext in ("*.icm", "*.icc"):
        found = list(ref_dir.glob(ext))
        if found:
            return found[0]
    return None


def _list_ref_profiles(ref_dir: Path | None) -> list[Path]:
    """Return all .icm/.icc profiles in ref dir, ClayRGB1998 first."""
    if not ref_dir:
        return []
    profiles = []
    for ext in ("*.icm", "*.icc"):
        profiles.extend(ref_dir.glob(ext))
    # Sort alphabetically but put ClayRGB1998 first
    profiles.sort(key=lambda p: p.name.lower())
    clay = ref_dir / "ClayRGB1998.icm"
    if clay in profiles:
        profiles.remove(clay)
        profiles.insert(0, clay)
    return profiles


def _read_icc_description(path: Path) -> str | None:
    """Extract the ASCII description from an ICC profile's 'desc' tag."""
    try:
        with open(path, "rb") as f:
            data = f.read()
        # ICC header is 128 bytes
        # Tag count at offset 128
        if len(data) < 132:
            return None
        tag_count = int.from_bytes(data[128:132], "big")
        # Tag table starts at 132, each entry is 12 bytes (sig, offset, size)
        for i in range(tag_count):
            entry_offset = 132 + i * 12
            if entry_offset + 12 > len(data):
                break
            tag_sig = data[entry_offset : entry_offset + 4]
            if tag_sig == b"desc":
                tag_offset = int.from_bytes(
                    data[entry_offset + 4 : entry_offset + 8], "big"
                )
                # desc tag structure: type (4) + reserved (4) + ASCII count (4) + ASCII string
                if tag_offset + 12 > len(data):
                    break
                type_sig = data[tag_offset : tag_offset + 4]
                if type_sig != b"desc":
                    break
                ascii_count = int.from_bytes(
                    data[tag_offset + 8 : tag_offset + 12], "big"
                )
                if ascii_count > 0 and tag_offset + 12 + ascii_count <= len(data):
                    ascii_str = data[tag_offset + 12 : tag_offset + 12 + ascii_count - 1]
                    return ascii_str.decode("ascii", "replace").strip()
        return None
    except (OSError, UnicodeDecodeError, ValueError):
        return None


class ProfilePage(QWidget):
    """UI for creating ICC profiles via ArgyllCMS colprof."""

    profileGenerated = Signal(Path)

    def __init__(self, workspace: Path, parent=None) -> None:
        super().__init__(parent)
        self.workspace = workspace
        self._install = discover.discover()
        self._proc: QProcess | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(23, 23, 23, 12)
        root.setSpacing(16)

        title = QLabel("Create Profile")
        title.setStyleSheet("font-size: 20px; font-weight: 600;")
        root.addWidget(title)

        # --- Measurement data (.ti3) ------------------------------------
        ti3_group = QGroupBox("")
        ti3_group.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum
        )
        ti3_form = QFormLayout(ti3_group)
        ti3_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        ti3_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow
        )
        ti3_form.setContentsMargins(8, 4, 8, 4)

        ti3_row = QHBoxLayout()
        self.ti3_edit = QLineEdit()
        self.ti3_edit.setStyleSheet("background-color: white;")
        self.ti3_browse = QPushButton("Browse\u2026")
        ti3_row.addWidget(self.ti3_edit, stretch=1)
        ti3_row.addWidget(self.ti3_browse)
        ti3_form.addRow("Measurement data (.ti3):", ti3_row)

        root.addWidget(ti3_group)

        # --- Gamut mapping profile --------------------------------------
        ref_group = QGroupBox("")
        ref_group.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum
        )
        ref_form = QFormLayout(ref_group)
        ref_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        ref_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow
        )
        ref_form.setContentsMargins(8, 4, 8, 4)

        ref_row = QHBoxLayout()
        self.ref_combo = QComboBox()
        self.ref_combo.setMinimumWidth(300)
        self.ref_browse = QPushButton("Browse\u2026")
        ref_row.addWidget(self.ref_combo, stretch=1)
        ref_row.addWidget(self.ref_browse)
        ref_form.addRow("Gamut mapping profile:", ref_row)

        # Profile quality
        self.quality_combo = QComboBox()
        self.quality_combo.addItem("Low", "l")
        self.quality_combo.addItem("Medium", "m")
        self.quality_combo.addItem("High", "h")
        self.quality_combo.setCurrentIndex(2)  # Default to High
        ref_form.addRow("Profile quality:", self.quality_combo)

        # Smoothing (-r parameter) - higher values for more smoothing
        self.smoothing_spin = QDoubleSpinBox()
        self.smoothing_spin.setRange(0.0, 1.5)
        self.smoothing_spin.setValue(0.5)  # Default -r 0.5 (avgdev)
        self.smoothing_spin.setDecimals(2)
        self.smoothing_spin.setSingleStep(0.05)
        ref_form.addRow("Smoothing (-r):", self.smoothing_spin)

        root.addWidget(ref_group)

        # --- Profile name ------------------------------------------------
        name_group = QGroupBox("")
        name_group.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum
        )
        name_form = QFormLayout(name_group)
        name_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        name_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow
        )
        name_form.setContentsMargins(8, 4, 8, 4)

        self.desc_edit = QLineEdit()
        self.desc_edit.setStyleSheet("background-color: white;")
        name_form.addRow("Profile description:", self.desc_edit)

        root.addWidget(name_group)

        # --- Generate / Cancel buttons -----------------------------------
        button_row = QHBoxLayout()
        self.generate_btn = QPushButton("Create Profile")
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setVisible(False)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFixedWidth(120)
        self.progress_bar.setVisible(False)
        
        button_row.addWidget(self.generate_btn)
        button_row.addWidget(self.cancel_btn)
        button_row.addWidget(self.progress_bar)
        button_row.addStretch()
        root.addLayout(button_row)

        root.addSpacing(12)

        # --- Toggle for preview / output ----------------------------------
        self.show_preview_check = QCheckBox(
            "Show command preview and ArgyllCMS output"
        )
        root.addWidget(self.show_preview_check)

        self.cmd_preview = QPlainTextEdit()
        self.cmd_preview.setReadOnly(True)
        self.cmd_preview.setMaximumHeight(80)
        self.cmd_preview.setStyleSheet(
            "font-family: monospace; background: #f4f4f4; color: #222;"
        )
        self.cmd_preview.setVisible(False)
        root.addWidget(self.cmd_preview)

        # --- Output ------------------------------------------------------
        self.console = LogConsole()
        self.console.setFixedHeight(180)
        self.console.setVisible(False)
        root.addWidget(self.console)

        root.addStretch(1)

        # Connections
        self.ti3_browse.clicked.connect(self._browse_ti3)
        self.ref_browse.clicked.connect(self._browse_ref_profile)
        self.generate_btn.clicked.connect(self._on_generate)
        self.cancel_btn.clicked.connect(self._on_cancel)
        self.show_preview_check.toggled.connect(self.cmd_preview.setVisible)
        self.show_preview_check.toggled.connect(self.console.setVisible)
        self.ti3_edit.textChanged.connect(self._refresh_preview)
        self.ref_combo.currentTextChanged.connect(self._refresh_preview)
        self.quality_combo.currentTextChanged.connect(self._refresh_preview)
        self.smoothing_spin.valueChanged.connect(self._refresh_preview)
        self.desc_edit.textChanged.connect(self._refresh_preview)

        # Discover default reference profile
        self._discover_default_ref()
        self._refresh_preview()

    # ----------------------------------------------------------- discovery
    def _discover_default_ref(self) -> None:
        ref_dir = _find_argyll_ref_dir(
            self._install.colprof if self._install else None
        )
        profiles = _list_ref_profiles(ref_dir)
        
        # Fallback chain: AdobeRGB → sRGB
        if not profiles:
            adobe = _find_adobe_rgb_profile()
            if adobe:
                profiles = [adobe]
            else:
                srgb = _find_srgb_profile()
                if srgb:
                    profiles = [srgb]
        
        for profile in profiles:
            desc = _read_icc_description(profile)
            if desc:
                display_text = f"{profile.name} — {desc}"
            else:
                display_text = profile.name
            self.ref_combo.addItem(display_text, str(profile))
        # Select ClayRGB1998 if available, otherwise first item
        if profiles:
            clay_idx = self.ref_combo.findText("ClayRGB1998.icm", Qt.MatchFlag.MatchStartsWith)
            if clay_idx >= 0:
                self.ref_combo.setCurrentIndex(clay_idx)

    # ----------------------------------------------------------- browsing
    def set_ti3_path(self, path: Path | None) -> None:
        """Populate the .ti3 path from measurement page."""
        if path and path.exists():
            self.ti3_edit.setText(str(path))
            if not self.desc_edit.text().strip():
                self.desc_edit.setText(path.stem)

    def _browse_ti3(self) -> None:
        current = self.ti3_edit.text().strip()
        start_dir = str(Path(current).parent) if current else str(self.workspace)
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select .ti3 measurement data",
            current or start_dir,
            "Argyll measurement (*.ti3);;All files (*)",
        )
        if path:
            self.ti3_edit.setText(path)
            if not self.desc_edit.text().strip():
                self.desc_edit.setText(Path(path).stem)

    def _browse_ref_profile(self) -> None:
        """Browse for a custom gamut mapping profile."""
        current = self.ref_combo.currentData()
        start_dir = str(Path(current).parent) if current else str(self.workspace)
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select gamut mapping profile",
            start_dir,
            "ICC/ICM profiles (*.icc *.icm);;All files (*)",
        )
        if path:
            p = Path(path)
            desc = _read_icc_description(p)
            display_text = f"{p.name} — {desc}" if desc else p.name
            # Check if already in combo
            existing = self.ref_combo.findData(str(p))
            if existing < 0:
                self.ref_combo.addItem(display_text, str(p))
            self.ref_combo.setCurrentIndex(self.ref_combo.findData(str(p)))

    # ----------------------------------------------------------- preview
    def _build_args(self, name: str) -> list[str]:
        quality = self.quality_combo.currentData() or "h"
        args = ["-v", f"-q{quality}", "-cmt", "-dpp"]
        # Smoothing factor (-r) for handling measurement uncertainty
        smoothing = self.smoothing_spin.value()
        if abs(smoothing - 0.5) > 0.001:
            # Format without trailing zeros: 0.60 -> 0.6, 0.65 -> 0.65
            r_val = f"{smoothing:.10f}".rstrip('0').rstrip('.')
            args.append(f"-r{r_val}")
        desc = self.desc_edit.text().strip()
        if desc:
            args.extend(["-D", desc])
        # Only add -S if a gamut mapping profile is selected.
        # If none available, colprof will use relative colorimetric for all intents.
        ref = self.ref_combo.currentData()
        if ref:
            args.extend(["-S", ref])
        args.append(name)
        return args

    def _get_profile_name(self) -> str:
        """Derive output profile name from ti3 filename."""
        ti3 = self.ti3_edit.text().strip()
        if ti3:
            return Path(ti3).stem
        return "profile"

    def _refresh_preview(self) -> None:
        if not self._install:
            self.cmd_preview.setPlainText("ArgyllCMS not found.")
            return

        ti3 = self.ti3_edit.text().strip()
        if not ti3:
            self.cmd_preview.setPlainText("Select a .ti3 file first.")
            return

        ti3_path = Path(ti3)
        work_dir = str(ti3_path.parent) if ti3 else ""
        name = self._get_profile_name()

        lines = []
        if work_dir:
            lines.append(f"cd {work_dir}")
        lines.append(
            f"{self._install.colprof} "
            f"{' '.join(shlex.quote(a) for a in self._build_args(name))}"
        )
        self.cmd_preview.setPlainText("\n".join(lines))

    # ----------------------------------------------------------- generation
    def _on_generate(self) -> None:
        if not self._install:
            return

        ti3_str = self.ti3_edit.text().strip()
        if not ti3_str:
            QMessageBox.warning(self, "Missing data", "Please select a .ti3 file.")
            return

        ti3_path = Path(ti3_str)
        if not ti3_path.exists():
            QMessageBox.warning(
                self, "Bad file", f"File does not exist:\n{ti3_path}"
            )
            return

        work_dir = str(ti3_path.parent)
        name = self._get_profile_name()

        self.console.clear()
        self.console.append_line(f"Working folder: {work_dir}")
        self.console.append_line(f"Profile name:   {name}")

        args = self._build_args(name)

        if self._proc:
            self._proc.kill()
            self._proc = None

        self._proc = QProcess(self)
        self._proc.setProcessChannelMode(
            QProcess.ProcessChannelMode.MergedChannels
        )
        self._proc.setWorkingDirectory(work_dir)
        self._proc.readyReadStandardOutput.connect(self._on_proc_stdout)
        self._proc.finished.connect(self._on_proc_finished)
        self.console.append_line(
            f"\n$ {self._install.colprof} "
            f"{' '.join(shlex.quote(a) for a in args)}"
        )
        self._proc.start(str(self._install.colprof), args)
        
        self.generate_btn.setVisible(False)
        self.cancel_btn.setVisible(True)
        self.progress_bar.setVisible(True)

    def _on_proc_stdout(self) -> None:
        if not self._proc:
            return
        data = bytes(self._proc.readAllStandardOutput()).decode(
            "utf-8", "ignore"
        )
        for line in data.splitlines():
            self.console.append_line(line)

    def _on_proc_finished(self, code: int, _status) -> None:
        self.console.append_line(f"(exit code {code})")
        self._proc = None
        self.generate_btn.setVisible(True)
        self.cancel_btn.setVisible(False)
        self.progress_bar.setVisible(False)
        if code == 0:
            ti3_path = Path(self.ti3_edit.text().strip())
            name = self._get_profile_name()
            icc_path = ti3_path.parent / f"{name}.icc"
            self.profileGenerated.emit(icc_path)

    def _on_cancel(self) -> None:
        if self._proc:
            self._proc.kill()
            self._proc = None
        self.console.append_line("Cancelled.")
        self.generate_btn.setVisible(True)
        self.cancel_btn.setVisible(False)
        self.progress_bar.setVisible(False)
