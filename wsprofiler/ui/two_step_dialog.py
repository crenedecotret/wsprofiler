"""Modal guided wizard for two-pass ICC profiling.

The user only interacts on three steps:
    Step 1:  Choose device/paper → generate first chart
    Step 2:  Measure first chart (embedded MeasurementPage)
    Step 3:  Measure second chart (embedded MeasurementPage)

Automated processing (intermediate profile, second-chart generation,
TI3 merging, and final profile) runs silently between steps.
"""
from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal, Slot, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .pages.generate_page import _PATCHES_PER_PAGE
from .pages.measurement_page import MeasurementPage
from .pages.profile_page import _find_adobe_rgb_profile, _find_argyll_ref_dir, _list_ref_profiles, _read_icc_description
from .process_chain import ProcessChain, ProcessStep
from ..argyll import discover
from ..ti import ti3_combiner


PATTERS_PER_PAGE_RAW: dict[tuple[str, str, bool], int] = _PATCHES_PER_PAGE


# ------------------------------------------------------------------- helpers


def _format_size(width: int, height: int) -> str:
    return f"{width}x{height}"


def _find_srgb_profile() -> Optional[Path]:
    candidates = [
        "sRGB.icm", "sRGB.icc", "sRGB1996.icm", "sRGB1996.icc",
        "sRGB_IEC61966-2-1.icm", "sRGB_IEC61966-2-1.icc",
    ]
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
                return profile_path
    return None


def _find_clay_rgb() -> Optional[str]:
    install = discover.discover()
    ref_dir = _find_argyll_ref_dir(install.colprof if install else None)
    if ref_dir:
        clay = ref_dir / "ClayRGB1998.icm"
        if clay.exists():
            return str(clay)
    adobe = _find_adobe_rgb_profile()
    if adobe:
        return str(adobe)
    srgb = _find_srgb_profile()
    if srgb:
        return str(srgb)
    return None


# ------------------------------------------------------------------- dialog


class TwoStepWizardDialog(QDialog):
    PAGE_GENERATE = 0
    PAGE_MEASURE1 = 1
    PAGE_AUTO1 = 2
    PAGE_MEASURE2 = 3
    PAGE_AUTO2 = 4
    PAGE_DONE = 5

    def __init__(self, workspace: Path, parent=None) -> None:
        super().__init__(parent)
        self.workspace = workspace
        self._install = discover.discover()

        # state
        self._current_step = 0
        self._device = None
        self._paper = None
        self._chart1_ti2: Optional[Path] = None
        self._chart1_ti3: Optional[Path] = None
        self._precond_icc: Optional[Path] = None
        self._chart2_ti2: Optional[Path] = None
        self._chart2_ti3: Optional[Path] = None
        self._combined_ti3: Optional[Path] = None
        self._final_icc: Optional[Path] = None

        # master paper list for filtering
        self._all_papers = [
            ("A4", "A4  [210 × 297 mm]"),
            ("A4R", "A4R  [297 × 210 mm]"),
            ("A3", "A3  [297 × 420 mm]"),
            ("A3+", "A3+/SuperB  [483 × 329 mm]"),
            ("A2", "A2  [420 × 594 mm]"),
            ("Letter", "Letter  [215.9 × 279.4 mm]"),
            ("LetterR", "LetterR  [279.4 × 215.9 mm]"),
            ("Legal", "Legal  [215.9 × 355.6 mm]"),
            ("4x6", "4×6  [101.6 × 152.4 mm]"),
            ("11x17", "11×17  [279.4 × 431.8 mm]"),
        ]

        self._proc: Optional[list[str]] = None  # track running step name
        self._chain: Optional[ProcessChain] = None

        # guard against duplicate signal connections
        self._meas1_connected = False
        self._meas2_connected = False
        self._cancelled = False

        self.setWindowTitle("Two-Step Profile Wizard")
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        # title bar
        self._title_label = QLabel()
        self._title_label.setStyleSheet("font-size: 18px; font-weight: 700;")
        root.addWidget(self._title_label)

        # content stack
        self._stack = QStackedWidget()
        root.addWidget(self._stack, stretch=1)

        # button bar
        btn_row = QHBoxLayout()
        self._back_btn = QPushButton("← Back")
        self._cancel_btn = QPushButton("Cancel")
        self._next_btn = QPushButton("Next →")
        self._generate_btn = QPushButton("Generate")
        self._generate_btn.setStyleSheet("font-weight: bold; min-height: 36px;")
        self._generate_btn.setVisible(False)

        btn_row.addWidget(self._back_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._cancel_btn)
        btn_row.addWidget(self._generate_btn)
        btn_row.addWidget(self._next_btn)
        root.addLayout(btn_row)

        # build pages ------------------------------------------------------
        self._setup_generate_page()
        self._setup_measure1_page()
        self._setup_auto1_page()
        self._setup_measure2_page()
        self._setup_auto2_page()
        self._setup_done_page()

        # wiring -----------------------------------------------------------
        self._back_btn.clicked.connect(self._on_back)
        self._cancel_btn.clicked.connect(self._on_cancel)
        self._next_btn.clicked.connect(self._on_next)
        self._generate_btn.clicked.connect(self._on_generate)

        self._go_to_page(self.PAGE_GENERATE)

        # sensible dialog size
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            w = min(1400, max(900, avail.width() - 200))
            h = min(900, max(650, avail.height() - 200))
        else:
            w, h = 1200, 800
        self.resize(w, h)

    # ---------------------------------------------------------- page setup

    def _setup_generate_page(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(10)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._instr_combo = QComboBox()
        for code, label in [
            ("i1", "i1Pro (i1)"),
            ("3p", "i1Pro3+ (3p)"),
            ("CM", "ColorMunki (CM)"),
        ]:
            self._instr_combo.addItem(label, code)
        form.addRow("Instrument:", self._instr_combo)

        self._paper_combo = QComboBox()
        form.addRow("Paper:", self._paper_combo)

        self._target_edit = QLineEdit(str(self.workspace / "target"))

        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_target)
        target_row = QHBoxLayout()
        target_row.addWidget(self._target_edit, stretch=1)
        target_row.addWidget(browse_btn)
        form.addRow("Target:", target_row)

        self._summary_label = QLabel()
        self._summary_label.setStyleSheet("color: #8a8ea0; font-style: italic;")
        form.addRow("", self._summary_label)

        self._instr_combo.currentIndexChanged.connect(self._update_paper_options)
        self._paper_combo.currentIndexChanged.connect(self._refresh_summary)
        self._update_paper_options()
        self._refresh_summary()

        layout.addLayout(form)

        # console output area
        self._gen_console = QPlainTextEdit()
        self._gen_console.setReadOnly(True)
        self._gen_console.setMaximumHeight(220)
        self._gen_console.setStyleSheet("font-family: monospace; background: #f4f4f4; color: #222;")
        self._gen_console.setVisible(False)

        self._gen_show_check = QCheckBox("Show output")
        self._gen_show_check.setVisible(False)
        self._gen_show_check.toggled.connect(self._gen_console.setVisible)

        layout.addWidget(self._gen_show_check)
        layout.addWidget(self._gen_console)
        layout.addStretch(1)

        self._stack.insertWidget(self.PAGE_GENERATE, page)

    def _update_paper_options(self) -> None:
        """Filter paper options based on the selected instrument."""
        instr = self._instr_combo.currentData() or "i1"
        
        # Remember current selection if possible
        current_paper = self._paper_combo.currentData()

        self._paper_combo.clear()
        for code, desc in self._all_papers:
            # Disallow 4x6 for i1Pro3+ (3p) and ColorMunki (CM)
            if code == "4x6" and instr in ("3p", "CM"):
                continue
            self._paper_combo.addItem(desc, code)
        
        # Restore selection or default to Letter (index 5 in full list, index 4 in filtered)
        if current_paper:
            idx = self._paper_combo.findData(current_paper)
            if idx >= 0:
                self._paper_combo.setCurrentIndex(idx)
            else:
                # Default to Letter if available
                letter_idx = self._paper_combo.findData("Letter")
                if letter_idx >= 0:
                    self._paper_combo.setCurrentIndex(letter_idx)
                else:
                    self._paper_combo.setCurrentIndex(0)
        else:
            # Default to Letter if available
            letter_idx = self._paper_combo.findData("Letter")
            if letter_idx >= 0:
                self._paper_combo.setCurrentIndex(letter_idx)
            else:
                self._paper_combo.setCurrentIndex(0)

    def _refresh_summary(self) -> None:
        """Update patch count summary."""
        instr = self._instr_combo.currentData() or "i1"
        paper = self._paper_combo.currentData() or "A3"
        dd = False
        ppg = PATTERS_PER_PAGE_RAW.get((instr, paper, dd), 400)
        total = ppg
        if paper == "4x6":
            detail = "Auto white/black (targen defaults)"
        else:
            white = 4
            black = 4
            gray = self._default_gray_count()
            detail = f"{white} white, {black} black"
            if gray:
                detail += f", {gray} gray"
        self._summary_label.setText(f"≈ {total} total patches ({ppg}/page) · {detail}")

    def _default_gray_count(self) -> int:
        instr = self._instr_combo.currentData() or "i1"
        paper = self._paper_combo.currentData() or "A3"
        if paper in ("A3+", "A2") and instr not in ("CM", "3p"):
            return 128
        if instr in ("CM", "3p"):
            if paper in ("A3", "A3+", "A2"):
                return 51
            return 21
        return 51

    def _browse_target(self) -> None:
        current = self._target_edit.text().strip()
        path, _ = QFileDialog.getSaveFileName(
            self, "Select target file",
            current or str(self.workspace / "target"),
        )
        if path:
            p = Path(path)
            if p.suffix in (".ti1", ".ti2", ".ti3"):
                path = str(p.with_suffix(""))
            self._target_edit.setText(path)

    def _setup_measure1_page(self) -> None:
        self._meas1 = MeasurementPage(self.workspace)
        self._meas1.load_button.setVisible(False)
        self._meas1.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._stack.insertWidget(self.PAGE_MEASURE1, self._meas1)

    def _setup_auto1_page(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 24, 24, 24)

        self._auto1_status = QLabel("Processing…")
        self._auto1_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._auto1_status.setStyleSheet("font-size: 18px; color: #555;")
        layout.addWidget(self._auto1_status)

        self._auto1_show_check = QCheckBox("Show output")
        self._auto1_show_check.setVisible(True)

        self._auto1_console = QPlainTextEdit()
        self._auto1_console.setReadOnly(True)
        self._auto1_console.setStyleSheet("font-family: monospace; background: #f4f4f4; color: #222;")
        self._auto1_console.setVisible(False)

        self._auto1_show_check.toggled.connect(self._auto1_console.setVisible)

        layout.addWidget(self._auto1_show_check)
        layout.addWidget(self._auto1_console, stretch=1)
        self._stack.insertWidget(self.PAGE_AUTO1, page)

    def _setup_measure2_page(self) -> None:
        self._meas2 = MeasurementPage(self.workspace)
        self._meas2.load_button.setVisible(False)
        self._meas2.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._stack.insertWidget(self.PAGE_MEASURE2, self._meas2)

    def _setup_auto2_page(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 24, 24, 24)

        self._auto2_status = QLabel("Processing…")
        self._auto2_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._auto2_status.setStyleSheet("font-size: 18px; color: #555;")
        layout.addWidget(self._auto2_status)

        self._auto2_show_check = QCheckBox("Show output")
        self._auto2_show_check.setVisible(True)

        self._auto2_console = QPlainTextEdit()
        self._auto2_console.setReadOnly(True)
        self._auto2_console.setStyleSheet("font-family: monospace; background: #f4f4f4; color: #222;")
        self._auto2_console.setVisible(False)

        self._auto2_show_check.toggled.connect(self._auto2_console.setVisible)

        layout.addWidget(self._auto2_show_check)
        layout.addWidget(self._auto2_console, stretch=1)
        self._stack.insertWidget(self.PAGE_AUTO2, page)

    def _setup_done_page(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(50, 50, 50, 50)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel("Profile Complete")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 22px; font-weight: 700;")
        layout.addWidget(title)

        self._done_path_label = QLabel()
        self._done_path_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._done_path_label.setStyleSheet("font-size: 14px; color: #1F8A4C; margin-top: 12px;")
        layout.addWidget(self._done_path_label)

        layout.addStretch()
        self._stack.insertWidget(self.PAGE_DONE, page)

    def _go_to_page(self, idx: int) -> None:
        self._stack.setCurrentIndex(idx)
        self._update_buttons(idx)

        if idx == self.PAGE_GENERATE:
            self._title_label.setText("Step 1 of 3: Generate First Chart")
            self._generate_btn.setText("Generate")
            self._generate_btn.setVisible(True)
            self._generate_btn.setEnabled(self._install is not None)
            self._next_btn.setVisible(False)
        elif idx == self.PAGE_MEASURE1:
            self._title_label.setText("Step 2 of 3: Measure First Chart")
            if self._chart1_ti2:
                self._meas1.load_ti2(self._chart1_ti2)
            if not self._meas1_connected:
                self._meas1.measurementsComplete.connect(self._on_meas1_complete)
                self._meas1_connected = True
        elif idx == self.PAGE_AUTO1:
            self._title_label.setText("Processing…")
        elif idx == self.PAGE_MEASURE2:
            self._title_label.setText("Step 3 of 3: Measure Second Chart")
            if self._chart2_ti2:
                self._meas2.load_ti2(self._chart2_ti2)
            if not self._meas2_connected:
                self._meas2.measurementsComplete.connect(self._on_meas2_complete)
                self._meas2_connected = True
        elif idx == self.PAGE_AUTO2:
            self._title_label.setText("Creating Final Profile…")
        elif idx == self.PAGE_DONE:
            self._title_label.setText("Done")

    def _update_buttons(self, idx: int) -> None:
        is_generate = idx == self.PAGE_GENERATE
        is_measure = idx in (self.PAGE_MEASURE1, self.PAGE_MEASURE2)
        is_auto = idx in (self.PAGE_AUTO1, self.PAGE_AUTO2)
        is_done = idx == self.PAGE_DONE

        self._back_btn.setVisible(is_measure)
        self._cancel_btn.setVisible(not is_done)
        self._generate_btn.setVisible(is_generate)
        self._next_btn.setVisible(not is_auto and not is_generate)

        if is_auto:
            self._back_btn.setVisible(False)

        if is_done:
            self._next_btn.setText("Close")
        else:
            self._next_btn.setText("Next →")

        if idx == self.PAGE_GENERATE:
            self._next_btn.setVisible(False)
            self._generate_btn.setVisible(True)
            self._generate_btn.setEnabled(self._install is not None)

        if idx == self.PAGE_MEASURE1:
            self._next_btn.setEnabled(self._chart1_ti3 is not None)
        elif idx == self.PAGE_MEASURE2:
            self._next_btn.setEnabled(self._chart2_ti3 is not None)
        else:
            if not is_generate and not is_done:
                self._next_btn.setEnabled(True)

    # ----------------------------------------------------------- handlers

    def _on_back(self) -> None:
        cur = self._stack.currentIndex()
        if cur == self.PAGE_MEASURE1:
            self._go_to_page(self.PAGE_GENERATE)
        elif cur == self.PAGE_MEASURE2:
            self._go_to_page(self.PAGE_MEASURE1)

    def _on_cancel(self) -> None:
        self._cancelled = True
        if self._chain is not None and self._chain.is_running():
            self._chain.cancel()
            self._chain = None
        if self._proc is not None:
            self._proc.kill()
            self._proc = None
        self.reject()

    def _on_next(self) -> None:
        cur = self._stack.currentIndex()
        if cur == self.PAGE_GENERATE:
            self._go_to_page(self.PAGE_MEASURE1)
        elif cur == self.PAGE_MEASURE1:
            self._go_to_page(self.PAGE_AUTO1)
            self._run_auto1()
        elif cur == self.PAGE_MEASURE2:
            self._go_to_page(self.PAGE_AUTO2)
            self._run_auto2()
        elif cur == self.PAGE_DONE:
            self.accept()

    # --------------------------------------------------- generate step 1

    @staticmethod
    def _capture_stdout(proc, console: QPlainTextEdit) -> None:
        """Capture stdout to console (used by auto-processing chains)."""
        data = bytes(proc.readAllStandardOutput()).decode("utf-8", "ignore")
        for line in data.splitlines():
            console.appendPlainText(line)

    def _on_generate(self) -> None:
        """Run targen + printtarg for the first chart."""
        if not self._install:
            return
        if self._chain is not None and self._chain.is_running():
            return

        target = Path(self._target_edit.text().strip())
        if not target:
            QMessageBox.warning(self, "Missing target", "Please enter a target file path.")
            return
        if not target.parent.exists():
            QMessageBox.warning(self, "Bad folder", f"Folder does not exist:\n{target.parent}")
            return

        self._device = self._instr_combo.currentData()
        self._paper = self._paper_combo.currentData()

        self._gen_console.clear()
        self._gen_show_check.setVisible(True)
        self._gen_show_check.setChecked(False)
        self._gen_console.setVisible(False)
        self._generate_btn.setEnabled(False)
        self._generate_btn.setText("Generating…")
        self._gen_console.appendPlainText(f"Running targen + printtarg for {target}")

        # build args
        instr = self._device or "i1"
        paper = self._paper or "A3"
        dd = False
        ppg = PATTERS_PER_PAGE_RAW.get((instr, paper, dd), 400)
        total_patches = ppg

        if paper == "4x6":
            f_count = max(total_patches, 10)
            white = black = gray = 0
        else:
            f_count = max(total_patches, 10)
            white = 4
            black = 4
            gray = self._default_gray_count()

        name = target.name
        work_dir = str(target.parent)

        targen_args = ["-v", "-d2", "-G", f"-f{f_count}"]
        if white:
            targen_args.append(f"-e{white}")
        if black:
            targen_args.append(f"-B{black}")
        if gray:
            targen_args.append(f"-g{gray}")
        targen_args.append(name)

        printtarg_args = ["-v", f"-i{instr}", "-t300"]
        if paper == "A3+":
            printtarg_args.extend(["-p483x329", name])
        else:
            printtarg_args.extend([f"-p{paper}", name])

        self._gen_console.appendPlainText(f"cd {work_dir}")
        cmd1 = f"{self._install.targen} {' '.join(shlex.quote(a) for a in targen_args)}"
        self._gen_console.appendPlainText(f"$ {cmd1}")

        self._chain = ProcessChain(self)
        self._chain.set_error_handler(
            lambda msg: self._on_chain_error(msg, self._gen_console)
        )
        self._chain.set_completion_handler(self._on_generate_done)

        self._chain.add_step(ProcessStep(
            exe=self._install.targen,
            args=targen_args,
            work_dir=work_dir,
            on_finished=lambda code: None,
            console=self._gen_console,
        ))
        self._chain.add_step(ProcessStep(
            exe=self._install.printtarg,
            args=printtarg_args,
            work_dir=work_dir,
            on_finished=lambda code: None,
            console=self._gen_console,
        ))
        self._chain.start()

    def _on_chain_error(self, msg: str, console: QPlainTextEdit) -> None:
        """Handle a chain step failure."""
        console.appendPlainText(f"\n{msg}")
        self._generate_btn.setEnabled(True)
        self._generate_btn.setText("Generate")

    def _on_generate_done(self) -> None:
        """Handle successful completion of generate chain."""
        target = Path(self._target_edit.text().strip())
        ti2_path = target.with_suffix(".ti2")
        self._chart1_ti2 = ti2_path
        self._gen_console.appendPlainText(f"\nChart generated: {ti2_path}")
        self._generate_btn.setText("Generated ✓")
        self._generate_btn.setEnabled(False)

        # show print instructions then enable next
        msg = QMessageBox(self)
        msg.setWindowTitle("Chart Ready")
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setText(
            f"Chart generated at:\n{ti2_path}\n\n"
            "Please print the chart at original size with color "
            "management DISABLED, then click OK."
        )
        msg.exec()

        # show Next button
        self._generate_btn.setVisible(False)
        self._next_btn.setVisible(True)
        self._next_btn.setEnabled(True)

    # --------------------------------------------------- measurement steps

    @Slot(Path)
    def _on_meas1_complete(self, ti3_path: Path) -> None:
        self._chart1_ti3 = ti3_path
        self._next_btn.setEnabled(True)

    @Slot(Path)
    def _on_meas2_complete(self, ti3_path: Path) -> None:
        self._chart2_ti3 = ti3_path
        self._next_btn.setEnabled(True)

    # ---------------------------------------------- auto 1: precond + chart 2

    def _run_auto1(self) -> None:
        """Create intermediate profile, then generate chart 2."""
        self._auto1_status.setText("Creating intermediate profile…")

        from PySide6.QtCore import QProcess
        ti3 = str(self._chart1_ti3)
        work_dir = str(self._chart1_ti3.parent)
        name = self._chart1_ti3.stem

        args = ["-v", "-ql", "-cmt", "-dpp", "-r0.75", "-D", "Intermediate Precond Profile"]
        args.append(name)

        self._proc = QProcess(self)
        self._proc.setWorkingDirectory(work_dir)
        self._proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._proc.finished.connect(self._on_precond_done)
        self._proc.readyReadStandardOutput.connect(
            lambda p=self._proc, c=self._auto1_console: self._capture_stdout(p, c)
        )
        self._proc.start(str(self._install.colprof), args)

    def _on_precond_done(self, code: int) -> None:
        if code != 0:
            self._auto1_status.setText("Intermediate profile failed — aborting.")
            self._proc = None
            return

        self._proc = None
        # rename to low-key name
        target = Path(self._target_edit.text().strip())
        icc_orig = target.with_suffix(".icc")
        precond_path = target.parent / "_precond_tmp.icc"
        if precond_path.exists():
            precond_path.unlink()
        if icc_orig.exists():
            icc_orig.rename(precond_path)
        self._precond_icc = precond_path

        self._auto1_status.setText("Generating second chart (preconditioned)…")

        # chart 2 generation
        instr = self._device or "i1"
        paper = self._paper or "A3"
        ppg = PATTERS_PER_PAGE_RAW.get((instr, paper, False), 400)
        total = ppg

        if paper == "4x6":
            f_cnt = max(total, 10)
            white = black = gray_val = 0
        else:
            f_cnt = max(total, 10)
            white = 4
            black = 4
            gray_val = self._default_gray_count()

        target2 = str(self._chart1_ti3.with_suffix("")) + "_precond"
        work_dir2 = str(self._chart1_ti3.parent)
        name2 = Path(target2).name

        targen_args = ["-v", "-d2", "-G", f"-f{f_cnt}"]
        if white:
            targen_args.append(f"-e{white}")
        if black:
            targen_args.append(f"-B{black}")
        if gray_val:
            targen_args.append(f"-g{gray_val}")
        targen_args.extend(["-c", str(precond_path), "-N0.75"])
        targen_args.append(name2)

        printtarg_args = ["-v", f"-i{instr}", "-t300"]
        if paper == "A3+":
            printtarg_args.extend(["-p483x329", name2])
        else:
            printtarg_args.extend([f"-p{paper}", name2])

        from PySide6.QtCore import QProcess
        self._proc = QProcess(self)
        self._proc.setWorkingDirectory(work_dir2)
        self._proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._auto1_targen_args = targen_args
        self._auto1_printtarg_args = printtarg_args
        self._auto1_work_dir = work_dir2
        self._auto1_target2 = Path(target2)
        self._proc.finished.connect(self._on_chart2_targen_done)
        self._proc.readyReadStandardOutput.connect(
            lambda p=self._proc, c=self._auto1_console: self._capture_stdout(p, c)
        )
        self._proc.start(str(self._install.targen), targen_args)

    def _on_chart2_targen_done(self, code: int) -> None:
        if code != 0:
            self._auto1_status.setText("Chart 2 generation failed (targen).")
            self._proc = None
            return

        from PySide6.QtCore import QProcess
        self._auto1_status.setText("Building chart 2 pages (printtarg)…")
        self._proc.finished.disconnect()
        self._proc.finished.connect(self._on_chart2_printtarg_done)
        self._proc.readyReadStandardOutput.disconnect()
        self._proc.readyReadStandardOutput.connect(
            lambda p=self._proc, c=self._auto1_console: self._capture_stdout(p, c)
        )
        self._proc.start(str(self._install.printtarg), self._auto1_printtarg_args)

    def _on_chart2_printtarg_done(self, code: int) -> None:
        self._proc = None
        if code != 0:
            self._auto1_status.setText("Chart 2 generation failed (printtarg).")
            return

        self._chart2_ti2 = self._auto1_target2.with_suffix(".ti2")
        self._go_to_page(self.PAGE_MEASURE2)

    # ---------------------------------------- auto 2: combine + final profile

    def _run_auto2(self) -> None:
        """Combine TI3 files, then create final profile."""
        self._auto2_status.setText("Combining measurements…")

        try:
            combined = self.workspace / "combined_measurements.ti3"
            ti3_combiner.combine(self._chart1_ti3, self._chart2_ti3, combined)
            self._combined_ti3 = combined
        except Exception as e:
            self._auto2_status.setText(f"Combination failed: {e}")
            return

        self._auto2_status.setText("Creating final profile…")
        from PySide6.QtCore import QProcess

        gamut = _find_clay_rgb()
        work_dir = str(combined.parent)
        name = combined.stem
        args = ["-v", "-qh", "-cmt", "-dpp", "-r0.75", "-D", "Final Two-Measurement Profile"]
        if gamut:
            args.extend(["-S", gamut])
        args.append(name)

        self._proc = QProcess(self)
        self._proc.setWorkingDirectory(work_dir)
        self._proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._proc.finished.connect(self._on_final_profile_done)
        self._proc.readyReadStandardOutput.connect(
            lambda p=self._proc, c=self._auto2_console: self._capture_stdout(p, c)
        )
        self._proc.start(str(self._install.colprof), args)

    def _on_final_profile_done(self, code: int) -> None:
        self._proc = None
        if code != 0:
            self._auto2_status.setText("Final profile creation failed.")
            return

        self._final_icc = self._combined_ti3.parent / f"{self._combined_ti3.stem}.icc"
        self._done_path_label.setText(str(self._final_icc))
        self._go_to_page(self.PAGE_DONE)

