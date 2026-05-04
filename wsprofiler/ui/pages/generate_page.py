"""Generate page: wraps ArgyllCMS targen (+ optional printtarg).

Provides the same patch-design knobs from the legacy ``pkpatches.py`` tool:
total patch count, white/black reference patches, optional grayscale ramp,
and optional ICC preconditioning profile.

Outputs:
    <working_folder>/<name>.ti1         (always)
    <working_folder>/<name>.ti2 + chart (if "Generate printable chart" enabled)
"""
from __future__ import annotations

import shlex
from pathlib import Path

from PySide6.QtCore import QProcess, QSettings, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
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
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...argyll import discover
from ..log_console import LogConsole


# Patches per page for each (instrument, paper) combination.
# Determined empirically by running printtarg with 400-patch test targets.
# Key: (instrument, paper, double_density)
_PATCHES_PER_PAGE: dict[tuple[str, str, bool], int] = {
    ("i1", "A4", False): 441, ("i1", "A4R", False): 512, ("i1", "A3", False): 672,
    ("i1", "A2", False): 987, ("i1", "Letter", False): 462, ("i1", "LetterR", False): 480,
    ("i1", "Legal", False): 462,    ("i1", "4x6", False): 80, ("i1", "11x17", False): 630,
    ("i1", "A3+", False): 1155,  # A3+/SuperB 483x329mm, measured
    ("3p", "A4", False): 90, ("3p", "A4R", False): 100, ("3p", "A3", False): 240,
    ("3p", "A2", False): 490, ("3p", "Letter", False): 98, ("3p", "LetterR", False): 90,
    ("3p", "Legal", False): 133, ("3p", "4x6", False): 18, ("3p", "11x17", False): 216,
    ("3p", "A3+", False): 288,  # A3+/SuperB 483x329mm, same as CM
    ("CM", "A4", False): 90, ("CM", "A4R", False): 100, ("CM", "A3", False): 240,
    ("CM", "A2", False): 490, ("CM", "Letter", False): 98, ("CM", "LetterR", False): 90,
    ("CM", "Legal", False): 133, ("CM", "4x6", False): 18, ("CM", "11x17", False): 216,
    ("CM", "A3+", False): 288,  # A3+/SuperB 483x329mm, measured
    # ColorMunki double density (-h)
    ("CM", "A4", True): 210, ("CM", "A4R", True): 180, ("CM", "A3", True): 460,
    ("CM", "A2", True): 1015, ("CM", "Letter", True): 196, ("CM", "LetterR", True): 171,
    ("CM", "Legal", True): 266, ("CM", "4x6", True): 30, ("CM", "11x17", True): 456,
    ("CM", "A3+", True): 578,  # A3+/SuperB 483x329mm with double density, measured
}


class GeneratePage(QWidget):
    """UI for generating ArgyllCMS test charts via targen (+printtarg)."""

    chartGenerated = Signal(Path)

    def __init__(self, workspace: Path, parent=None) -> None:
        super().__init__(parent)
        self.workspace = workspace
        self._install = discover.discover()
        self._proc: QProcess | None = None
        self._pending_steps: list[tuple[str, list[str], Path | None]] = []
        self._generated_target: Path | None = None
        self._generation_error: bool = False

        root = QVBoxLayout(self)
        root.setContentsMargins(23, 23, 23, 12)
        root.setSpacing(16)

        title = QLabel("Generate Chart")
        title.setStyleSheet("font-size: 20px; font-weight: 600;")
        root.addWidget(title)

        # --- Target --------------------------------------------------------
        target_group = QGroupBox("")
        target_group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        target_form = QFormLayout(target_group)
        target_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        target_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        target_form.setContentsMargins(8, 4, 8, 4)

        target_row = QHBoxLayout()
        self.target_edit = QLineEdit(str(workspace / "mytarget"))
        self.target_edit.setStyleSheet("background-color: white;")
        self.target_browse = QPushButton("Browse\u2026")
        target_row.addWidget(self.target_edit, stretch=1)
        target_row.addWidget(self.target_browse)
        target_form.addRow("Profile folder/name:", target_row)

        root.addWidget(target_group)

        # --- Chart layout -------------------------------------------------
        layout_group = QGroupBox("")
        layout_group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        layout_form = QFormLayout(layout_group)
        layout_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.instrument_combo = QComboBox()
        for code, label in [
            ("i1", "i1Pro (i1)"),
            ("3p", "i1Pro3+ (3p)"),
            ("CM", "ColorMunki (CM)"),
        ]:
            self.instrument_combo.addItem(label, code)
        layout_form.addRow("Instrument:", self.instrument_combo)

        self.double_density_check = QCheckBox("Double density (-h, ColorMunki only)")
        self.double_density_check.setChecked(True)
        self.double_density_check.setVisible(False)
        dd_row = QWidget()
        dd_layout = QHBoxLayout(dd_row)
        dd_layout.setContentsMargins(0, 0, 0, 0)
        dd_layout.addWidget(self.double_density_check)
        dd_layout.addStretch()
        dd_row.setFixedHeight(self.double_density_check.sizeHint().height())
        layout_form.addRow("", dd_row)

        self.paper_combo = QComboBox()
        for code, desc in [
            ("A4",      "A4  [210 x 297 mm]"),
            ("A4R",     "A4R  [297 x 210 mm]"),
            ("A3",      "A3  [297 x 420 mm]"),
            ("A3+",     "A3+/SuperB  [483 x 329 mm]"),
            ("A2",      "A2  [420 x 594 mm]"),
            ("Letter",  "Letter  [215.9 x 279.4 mm]"),
            ("LetterR", "LetterR  [279.4 x 215.9 mm]"),
            ("Legal",   "Legal  [215.9 x 355.6 mm]"),
            ("4x6",     "4x6  [101.6 x 152.4 mm]"),
            ("11x17",   "11x17  [279.4 x 431.8 mm]"),
        ]:
            self.paper_combo.addItem(desc, code)
        self.paper_combo.setCurrentIndex(5)  # default Letter
        layout_form.addRow("Paper size:", self.paper_combo)

        self.pages_spin = self._make_spin(1, 50, 1)
        self.pages_spin.setStyleSheet("background-color: white;")
        layout_form.addRow("Number of pages:", self.pages_spin)

        self.summary_label = QLabel()
        self.summary_label.setStyleSheet("color: #555; font-style: italic;")
        layout_form.addRow("", self.summary_label)

        root.addWidget(layout_group)

        # --- targen options ------------------------------------------------
        targen_group = QGroupBox("")
        targen_group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        form = QFormLayout(targen_group)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setContentsMargins(8, 4, 8, 4)

        # Preconditioning profile
        precond_row = QHBoxLayout()
        self.precond_check = QCheckBox("Preconditioning profile")
        self.precond_path = QLineEdit()
        self.precond_path.setStyleSheet("background-color: white;")
        self.precond_browse = QPushButton("Browse\u2026")
        precond_row.addWidget(self.precond_check)
        precond_row.addWidget(self.precond_path, stretch=1)
        precond_row.addWidget(self.precond_browse)
        form.addRow("", precond_row)

        root.addWidget(targen_group)

        # --- Generate / Cancel buttons -----------------------------------
        button_row = QHBoxLayout()
        self.generate_btn = QPushButton("Generate")
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setVisible(False)
        button_row.addWidget(self.generate_btn)
        button_row.addWidget(self.cancel_btn)
        button_row.addStretch()
        root.addLayout(button_row)
        root.addSpacing(12)

        # --- Command preview ---------------------------------------------
        self.show_preview_check = QCheckBox("Show command preview and ArgyllCMS output")
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

        self.show_preview_check.toggled.connect(self.cmd_preview.setVisible)
        self.show_preview_check.toggled.connect(self.console.setVisible)

        # --- Wiring ------------------------------------------------------
        self.target_edit.textChanged.connect(self._refresh_preview)
        self.precond_check.toggled.connect(self._refresh_preview)
        self.pages_spin.valueChanged.connect(self._refresh_preview)
        self.instrument_combo.currentIndexChanged.connect(self._on_instrument_changed)
        self.paper_combo.currentIndexChanged.connect(self._refresh_preview)
        self.double_density_check.toggled.connect(self._refresh_preview)
        self.precond_path.textChanged.connect(self._refresh_preview)
        self.precond_browse.clicked.connect(self._browse_precond)
        self.target_browse.clicked.connect(self._browse_target)
        self.generate_btn.clicked.connect(self._on_generate)
        self.cancel_btn.clicked.connect(self._on_cancel)

        if self._install is None:
            self.console.append_line(
                "ArgyllCMS not found on PATH - install argyll or set its bin dir."
            )
            self.generate_btn.setEnabled(False)

        self._refresh_preview()

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _make_spin(lo: int, hi: int, value: int) -> QSpinBox:
        s = QSpinBox()
        s.setRange(lo, hi)
        s.setValue(value)
        return s

    def _default_gray_count(self) -> int:
        """Recommended gray patch count for the current instrument / pages."""
        instrument = self.instrument_combo.currentData() or "i1"
        paper = self.paper_combo.currentData() or "A3"
        pages = self.pages_spin.value()
        # A3+ and A2 always include 128 grays for non-ColorMunki/i1Pro3+ devices
        if paper in ("A3+", "A2") and instrument not in ("CM", "3p"):
            return 128
        # ColorMunki and i1Pro3+ (3p): A3, A3+ and A2 get 51/128 grays, others get 21/51
        if instrument in ("CM", "3p"):
            if paper in ("A3", "A3+", "A2"):
                return 51 if pages == 1 else 128
            return 21 if pages == 1 else 51
        # i1Pro and others
        return 51 if pages == 1 else 128

    def _is_4x6(self) -> bool:
        return (self.paper_combo.currentData() or "A3") == "4x6"

    def _calc_patches(self) -> tuple[int, int, int, int]:
        """Return (f_count, white, black, gray) based on current UI."""
        instrument = self.instrument_combo.currentData() or "i1"
        paper = self.paper_combo.currentData() or "A3"
        pages = self.pages_spin.value()
        dd = self.double_density_check.isChecked() and instrument == "CM"
        ppg = _PATCHES_PER_PAGE.get((instrument, paper, dd), 400)
        total_patches = ppg * pages
        if paper == "4x6":
            # Too small for explicit B/W and gray; let targen decide
            return max(total_patches, 10), 0, 0, 0
        white = 4 + 2 * (pages - 1)
        black = 4 + 2 * (pages - 1)
        gray = 0 if self._is_4x6() else self._default_gray_count()
        # targen's -f is the TOTAL patch count; -e/-B/-g are subsets of that total
        return max(total_patches, 10), white, black, gray

    def _build_targen_args(self, name: str) -> list[str]:
        """Build targen args using just the target stem (no directory)."""
        f_count, white, black, gray = self._calc_patches()
        args = ["-v", "-d2", "-G", f"-f{f_count}"]
        if white:
            args.append(f"-e{white}")
        if black:
            args.append(f"-B{black}")
        if gray > 0:
            args.append(f"-g{gray}")
        if self.precond_check.isChecked() and self.precond_path.text().strip():
            args.extend(["-c", self.precond_path.text().strip(), "-N0.75"])
        args.append(name)
        return args

    def _build_printtarg_args(self, name: str) -> list[str]:
        """Build printtarg args using just the target stem (no directory)."""
        instrument = self.instrument_combo.currentData() or "i1"
        paper = self.paper_combo.currentData() or "A3"
        args = ["-v", f"-i{instrument}", "-t300"]
        if self.double_density_check.isChecked() and instrument == "CM":
            args.append("-h")
        # Custom paper sizes need -pWWWxHHH format
        if paper == "A3+":
            args.extend(["-p483x329", name])  # A3+/SuperB 483x329mm
        else:
            args.extend([f"-p{paper}", name])
        return args

    def _on_instrument_changed(self) -> None:
        instrument = self.instrument_combo.currentData() or "i1"
        self.double_density_check.setVisible(instrument == "CM")
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        # Update summary label
        f_count, white, black, gray = self._calc_patches()
        total = f_count  # -f is the total including white/black/gray
        instrument = self.instrument_combo.currentData() or "i1"
        paper = self.paper_combo.currentData() or "A3"
        dd = self.double_density_check.isChecked() and instrument == "CM"
        ppg = _PATCHES_PER_PAGE.get((instrument, paper, dd), 400)
        if self._is_4x6():
            detail = "Auto white/black (targen defaults)"
        else:
            detail = f"{white} white, {black} black"
            if gray:
                detail += f", {gray} gray"
        self.summary_label.setText(
            f"≈ {total} total patches  ({ppg}/page)  •  {detail}"
        )

        if not self._install:
            self.cmd_preview.setPlainText("(argyllcms not found)")
            return
        target = Path(self.target_edit.text().strip() or str(self.workspace / "mytarget"))
        work_dir = shlex.quote(str(target.parent))
        name = target.name
        targen = " ".join(
            shlex.quote(s) for s in [str(self._install.targen)] + self._build_targen_args(name)
        )
        printtarg = " ".join(
            shlex.quote(s)
            for s in [str(self._install.printtarg)] + self._build_printtarg_args(name)
        )
        self.cmd_preview.setPlainText(f"cd {work_dir}\n{targen}\n{printtarg}")

    def _browse_precond(self) -> None:
        last = QSettings().value("generate/last_precond", str(self.workspace))
        path, _ = QFileDialog.getOpenFileName(
            self, "Select preconditioning profile", str(last),
            "ICC profile (*.icc *.icm);;All files (*)",
        )
        if path:
            self.precond_path.setText(path)
            self.precond_check.setChecked(True)
            QSettings().setValue("generate/last_precond", str(Path(path).parent))
            self._refresh_preview()

    def _browse_target(self) -> None:
        current = self.target_edit.text().strip()
        start_dir = str(Path(current).parent) if current else str(self.workspace)
        path, _ = QFileDialog.getSaveFileName(
            self, "Select target file", current or str(self.workspace / "mytarget"),
            "All files (*)",
        )
        if path:
            # Strip any extension the user may have typed
            p = Path(path)
            if p.suffix in (".ti1", ".ti2", ".ti3"):
                path = str(p.with_suffix(""))
            self.target_edit.setText(path)
            self._refresh_preview()

    # ----------------------------------------------------------- generation
    def _on_generate(self) -> None:
        if not self._install:
            return
        target_str = self.target_edit.text().strip()
        if not target_str:
            QMessageBox.warning(self, "Missing target", "Please select a target file.")
            return
        self._generated_target = Path(target_str)
        self._generation_error = False
        target = self._generated_target
        if not target.parent.exists():
            QMessageBox.warning(self, "Bad folder", f"Folder does not exist:\n{target.parent}")
            return

        # Validate preconditioning file if enabled
        if self.precond_check.isChecked():
            pp = self.precond_path.text().strip()
            if not pp or not Path(pp).exists():
                QMessageBox.warning(
                    self, "Missing profile",
                    "Preconditioning is enabled but the ICC file is missing."
                )
                return

        self._work_dir = str(target.parent)
        name = target.name

        self.console.clear()
        self.console.append_line(f"Working folder: {self._work_dir}")
        self.console.append_line(f"Target stem:    {name}")

        # Queue the steps to run in order.
        self._pending_steps.clear()
        self._pending_steps.append(
            ("targen", self._build_targen_args(name), None)
        )
        self._pending_steps.append(
            ("printtarg", self._build_printtarg_args(name), None)
        )

        self.generate_btn.setVisible(False)
        self.cancel_btn.setVisible(True)
        self._run_next_step()

    def _run_next_step(self) -> None:
        if not self._pending_steps:
            self._on_all_done()
            return
        step_name, args, target = self._pending_steps.pop(0)

        # Map step name to executable
        exe = {
            "targen": str(self._install.targen),
            "printtarg": str(self._install.printtarg),
        }[step_name]

        if self._proc:
            self._proc.kill()
            self._proc = None

        self._proc = QProcess(self)
        self._proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._proc.setWorkingDirectory(self._work_dir)
        self._proc.readyReadStandardOutput.connect(self._on_proc_stdout)
        self._proc.finished.connect(self._on_proc_finished)
        self.console.append_line(f"\n$ {exe} {' '.join(shlex.quote(a) for a in args)}")
        self._proc.start(exe, args)

    def _on_proc_stdout(self) -> None:
        if not self._proc:
            return
        data = bytes(self._proc.readAllStandardOutput()).decode("utf-8", "ignore")
        for line in data.splitlines():
            self.console.append_line(line)

    def _on_proc_finished(self, code: int, _status) -> None:
        self.console.append_line(f"(exit code {code})")
        self._proc = None
        if code != 0:
            self._generation_error = True
            self._pending_steps.clear()
            self.console.append_line("Aborting due to non-zero exit code.")
            self._on_all_done()
            return
        self._run_next_step()

    def _on_cancel(self) -> None:
        self._generation_error = True
        self._pending_steps.clear()
        if self._proc:
            self._proc.kill()
            self._proc = None
        self.console.append_line("Cancelled.")
        self._on_all_done()

    def _on_all_done(self) -> None:
        self.generate_btn.setVisible(True)
        self.cancel_btn.setVisible(False)
        if not self._generation_error and self._generated_target is not None:
            ti2_path = self._generated_target.with_suffix(".ti2")
            self.chartGenerated.emit(ti2_path)

