"""Optimise page: generate add-on charts and build improved profiles.

This page is entered after the user has completed a full profile in the
main wizard, or after loading a .wsp save state. It supports multiple
optimisation passes, each generating a new add-on chart, measuring it,
and combining all measurements into an improved profile.
"""
from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal, QProcess
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...argyll import discover
from ...profiling.pass2_generator import max_selectable_patches
from ...ti import ti3_combiner
from ..log_console import LogConsole
from ..session_controller import SimpleSessionController, find_tiff_pages
from .measurement_page import MeasurementPage
from .profile_page import _find_adobe_rgb_profile, _find_argyll_ref_dir, _list_ref_profiles


# ------------------------------------------------------------------ helpers


def _find_srgb_profile() -> Path | None:
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


def _find_clay_rgb() -> str | None:
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


def _default_gray_count(instrument: str, paper: str) -> int:
    if paper in ("A3+", "A2") and instrument not in ("CM", "3p"):
        return 128
    if instrument in ("CM", "3p"):
        if paper in ("A3", "A3+", "A2"):
            return 51
        return 21
    return 51


# ------------------------------------------------------------------ page


class OptimisePage(QWidget):
    """Wizard page for running optimisation passes."""

    optimisationComplete = Signal(Path)
    passChartsGenerated = Signal(Path, int)

    # internal state indices
    STATE_NO_DATA = 0
    STATE_READY = 1
    STATE_GENERATING = 2
    STATE_MEASURE = 3
    STATE_BUILDING = 4
    STATE_DONE = 5

    def __init__(self, workspace: Path, parent=None) -> None:
        super().__init__(parent)
        self.workspace = workspace
        self._install = discover.discover()
        self._proc: QProcess | None = None

        # -- data ---------------------------------------------------------
        self._generate_config: dict[str, Any] = {}
        self._profile_configs: list[dict[str, Any]] = []
        self._original_pass: dict[str, Path | None] = {}
        self._optimisation_passes: list[dict[str, Path | None]] = []
        self._combined_ti3s: list[Path] = []
        self._target_stem: str = "session"

        # -- UI -----------------------------------------------------------
        root = QVBoxLayout(self)
        root.setContentsMargins(23, 23, 23, 12)
        root.setSpacing(16)

        title = QLabel("Optimise Profile")
        title.setStyleSheet("font-size: 20px; font-weight: 600;")
        root.addWidget(title)

        self._stack = QStackedWidget()
        root.addWidget(self._stack, stretch=1)

        self._setup_no_data_page()
        self._setup_ready_page()
        self._setup_generating_page()
        self._setup_measure_page()
        self._setup_building_page()
        self._setup_done_page()

        # console
        self._console = LogConsole()
        self._console.setFixedHeight(180)
        self._console.setVisible(False)
        root.addWidget(self._console)

        self._show_console_check = QCheckBox("Show output")
        self._show_console_check.setVisible(False)
        self._show_console_check.toggled.connect(self._console.setVisible)
        root.addWidget(self._show_console_check)

        self._go_to_state(self.STATE_NO_DATA)

    # ---------------------------------------------------------- page setup

    def _setup_no_data_page(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        msg = QLabel(
            "No profile data loaded.\n\n"
            "Complete a full profile first (Generate → Measure → Profile)."
        )
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        msg.setStyleSheet("font-size: 14px; color: #8a8ea0;")
        layout.addWidget(msg)

        layout.addStretch(1)
        self._stack.insertWidget(self.STATE_NO_DATA, page)

    def _setup_ready_page(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)

        self._status_label = QLabel()
        self._status_label.setStyleSheet("font-size: 14px;")
        layout.addWidget(self._status_label)

        # --- Chart settings (mirror GeneratePage) ------------------------
        chart_group = QGroupBox("")
        chart_group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        chart_form = QFormLayout(chart_group)
        chart_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._instr_combo = QComboBox()
        for code, label in [
            ("i1", "i1Pro (i1)"),
            ("3p", "i1Pro3+ (3p)"),
            ("CM", "ColorMunki (CM)"),
        ]:
            self._instr_combo.addItem(label, code)
        chart_form.addRow("Instrument:", self._instr_combo)

        self._dd_check = QCheckBox("Double density (-h, ColorMunki only)")
        self._dd_check.setChecked(False)
        self._dd_check.setVisible(False)
        self._nb_check = QCheckBox("Suppress left paperclip border (-L, i1Pro only)")
        self._nb_check.setChecked(False)
        self._nb_check.setVisible(False)
        self._options_row = QWidget()
        options_layout = QHBoxLayout(self._options_row)
        options_layout.setContentsMargins(0, 0, 0, 0)
        self._options_placeholder = QLabel("")
        self._options_placeholder.setMinimumHeight(self._dd_check.sizeHint().height())
        self._options_placeholder.setVisible(False)
        options_layout.addWidget(self._options_placeholder)
        options_layout.addWidget(self._dd_check)
        options_layout.addWidget(self._nb_check)
        options_layout.addStretch()
        chart_form.addRow("", self._options_row)

        self._paper_combo = QComboBox()
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
            self._paper_combo.addItem(desc, code)
        chart_form.addRow("Paper size:", self._paper_combo)

        self._pages_spin = QSpinBox()
        self._pages_spin.setRange(1, 5)
        self._pages_spin.setValue(1)
        chart_form.addRow("Number of pages:", self._pages_spin)

        self._summary_label = QLabel()
        self._summary_label.setStyleSheet("color: #8a8ea0; font-style: italic;")
        chart_form.addRow("", self._summary_label)

        layout.addWidget(chart_group)

        self._generate_addon_btn = QPushButton("Generate Add-on Chart")
        self._generate_addon_btn.setStyleSheet("font-weight: bold; min-height: 36px;")
        self._generate_addon_btn.clicked.connect(self._start_add_on_generation)
        layout.addWidget(self._generate_addon_btn)

        self._build_profile_btn = QPushButton("Build Optimised Profile")
        self._build_profile_btn.setStyleSheet("font-weight: bold; min-height: 36px;")
        self._build_profile_btn.setEnabled(False)
        self._build_profile_btn.clicked.connect(self._run_build_profile)
        layout.addWidget(self._build_profile_btn)

        # Profile settings editor
        settings_group = QWidget()
        settings_layout = QFormLayout(settings_group)
        settings_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._gamut_edit = QLineEdit()
        settings_layout.addRow("Gamut mapping profile:", self._gamut_edit)

        self._quality_combo = QWidget()  # placeholder; we'll use a simple line edit for now
        self._quality_edit = QLineEdit("High")
        settings_layout.addRow("Profile quality:", self._quality_edit)

        self._smoothing_spin = QSpinBox()
        self._smoothing_spin.setRange(0, 150)
        self._smoothing_spin.setValue(50)
        self._smoothing_spin.setSuffix(" (%)")
        settings_layout.addRow("Smoothing (-r):", self._smoothing_spin)

        self._desc_edit = QLineEdit()
        settings_layout.addRow("Profile description:", self._desc_edit)

        layout.addWidget(settings_group)
        layout.addStretch(1)
        self._stack.insertWidget(self.STATE_READY, page)

        # --- Wiring ------------------------------------------------------
        self._instr_combo.currentIndexChanged.connect(self._on_instr_changed)
        self._paper_combo.currentIndexChanged.connect(self._update_summary)
        self._pages_spin.valueChanged.connect(self._update_summary)
        self._dd_check.toggled.connect(self._update_summary)
        self._nb_check.toggled.connect(self._update_summary)

    def _setup_generating_page(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._gen_status = QLabel("Generating add-on chart…")
        self._gen_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._gen_status.setStyleSheet("font-size: 18px; color: #555;")
        layout.addWidget(self._gen_status)

        layout.addStretch(1)
        self._stack.insertWidget(self.STATE_GENERATING, page)

    def _setup_measure_page(self) -> None:
        self._meas_page = MeasurementPage(self.workspace)
        self._meas_page.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._meas_page.measurementsComplete.connect(self._on_measurements_complete)
        self._stack.insertWidget(self.STATE_MEASURE, self._meas_page)

    def _setup_building_page(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._build_status = QLabel("Building optimised profile…")
        self._build_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._build_status.setStyleSheet("font-size: 18px; color: #555;")
        layout.addWidget(self._build_status)

        layout.addStretch(1)
        self._stack.insertWidget(self.STATE_BUILDING, page)

    def _setup_done_page(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._done_label = QLabel("Optimisation complete!")
        self._done_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._done_label.setStyleSheet("font-size: 18px; font-weight: 700; color: #1F8A4C;")
        layout.addWidget(self._done_label)

        self._done_path_label = QLabel()
        self._done_path_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._done_path_label.setStyleSheet("font-size: 14px; margin-top: 8px;")
        layout.addWidget(self._done_path_label)

        self._optimise_again_btn = QPushButton("Optimise Again")
        self._optimise_again_btn.clicked.connect(self._on_optimise_again)
        layout.addWidget(self._optimise_again_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        layout.addStretch(1)
        self._stack.insertWidget(self.STATE_DONE, page)

    # ----------------------------------------------------------- state mgmt

    def _go_to_state(self, state: int) -> None:
        self._stack.setCurrentIndex(state)
        self._show_console_check.setVisible(
            state in (self.STATE_GENERATING, self.STATE_BUILDING)
        )
        if state == self.STATE_NO_DATA:
            self._status_label.setText("")

    # ----------------------------------------------------------- public API

    def set_original_pass_data(
        self,
        generate_config: dict[str, Any],
        profile_config: dict[str, Any],
        files: dict[str, Path],
    ) -> None:
        """Called by the Wizard when a profile has just been completed."""
        self._generate_config = dict(generate_config)
        self._profile_configs = [dict(profile_config)]
        self._original_pass = dict(files)
        self._optimisation_passes = []
        self._combined_ti3s = []
        self._apply_settings_to_ui(profile_config)
        self._update_ready_status()
        self._go_to_state(self.STATE_READY)

    def has_data(self) -> bool:
        """Return True if the page has enough data to run an optimisation."""
        return bool(self._original_pass.get("icc"))

    # ----------------------------------------------------------- .wsp load

    def load_wsp_session(self, wsp_path: Path) -> None:
        """Restore state from a .wsp archive.

        Note: prefer the Wizard's high-level :meth:`Wizard.load_wsp_session`,
        which also arranges the SessionManager's temp directory.
        """
        manifest, files = SimpleSessionController.load(wsp_path, self.workspace)

        self._generate_config = dict(manifest.get("generate_config", {}))
        self._profile_configs = list(manifest.get("profile_configs", []))
        optimisation_count = manifest.get("optimisation_count", 0)

        # Original pass
        self._original_pass = {
            "ti1": files.get("ti1"),
            "ti2": files.get("ti2"),
            "ti3": files.get("ti3"),
            "icc": files.get("icc"),
        }

        # Optimisation passes
        self._optimisation_passes = []
        self._combined_ti3s = []
        for i in range(1, optimisation_count + 1):
            self._optimisation_passes.append({
                "ti1": files.get(f"pass{i}_ti1"),
                "ti2": files.get(f"pass{i}_ti2"),
                "ti3": files.get(f"pass{i}_ti3"),
                "icc": files.get(f"pass{i}_icc"),
            })
            combined = files.get(f"combined{i}_ti3")
            if combined:
                self._combined_ti3s.append(combined)

        if self._profile_configs:
            self._apply_settings_to_ui(self._profile_configs[-1])

        self._update_ready_status()
        self._go_to_state(self.STATE_READY)

    def _apply_settings_to_ui(self, config: dict[str, Any]) -> None:
        """Populate the settings editor from a saved config dict."""
        self._gamut_edit.setText(config.get("gamut_profile") or "")
        quality = config.get("quality", "High")
        self._quality_edit.setText(quality)
        smoothing = config.get("smoothing", 0.5)
        self._smoothing_spin.setValue(int(smoothing * 100))
        self._desc_edit.setText(config.get("description", ""))

    def _collect_settings_from_ui(self) -> dict[str, Any]:
        """Gather current settings from the UI editor."""
        gamut = self._gamut_edit.text().strip() or None
        quality = self._quality_edit.text().strip() or "High"
        smoothing = self._smoothing_spin.value() / 100.0
        desc = self._desc_edit.text().strip()
        return {
            "gamut_profile": gamut,
            "quality": quality,
            "smoothing": smoothing,
            "description": desc,
        }

    # ----------------------------------------------------------- status

    def _on_instr_changed(self) -> None:
        instr = self._instr_combo.currentData() or "i1"
        self._dd_check.setVisible(instr == "CM")
        self._nb_check.setVisible(instr == "i1")
        self._options_placeholder.setVisible(instr not in ("CM", "i1"))
        self._update_summary()

    def _update_summary(self) -> None:
        from .generate_page import _PATCHES_PER_PAGE
        instr = self._instr_combo.currentData() or "i1"
        paper = self._paper_combo.currentData() or "A3"
        dd = self._dd_check.isChecked() and instr == "CM"
        nb = self._nb_check.isChecked() and instr == "i1"
        ppg = _PATCHES_PER_PAGE.get((instr, paper, dd, nb), 400)

        # Cap pages spinbox so total never exceeds candidate pool
        pool_max = max_selectable_patches()
        max_pages = max(1, pool_max // ppg)
        if self._pages_spin.maximum() != max_pages:
            self._pages_spin.setMaximum(max_pages)
        if self._pages_spin.value() > max_pages:
            self._pages_spin.setValue(max_pages)

        pages = self._pages_spin.value()
        total = ppg * pages

        # White/black/gray counts (same logic as generate_page)
        if paper == "4x6":
            detail = "Auto white/black (targen defaults)"
        else:
            white = 4 + 2 * (pages - 1)
            black = 4 + 2 * (pages - 1)
            gray = self._default_gray_count(instr, paper, pages)
            detail = f"{white} white, {black} black"
            if gray:
                detail += f", {gray} gray"

        self._summary_label.setText(f"≈ {total} total patches ({ppg}/page) · {detail}")

    def _default_gray_count(self, instrument: str, paper: str, pages: int = 1) -> int:
        if paper in ("A3+", "A2") and instrument not in ("CM", "3p"):
            return 128
        if instrument in ("CM", "3p"):
            if paper in ("A3", "A3+", "A2"):
                return 51 if pages == 1 else 128
            return 21 if pages == 1 else 51
        return 51 if pages == 1 else 128

    def _update_ready_status(self) -> None:
        pass_count = len(self._optimisation_passes)
        latest_icc = self._latest_icc_path()
        if pass_count == 0:
            text = "Original profile ready. Ready to generate first add-on chart."
            # Seed chart settings from the original generation config
            instr = self._generate_config.get("device", "i1")
            paper = self._generate_config.get("paper", "A3")
            dd = self._generate_config.get("double_density", False)
            nb = self._generate_config.get("no_border", False)

            idx = self._instr_combo.findData(instr)
            if idx >= 0:
                self._instr_combo.setCurrentIndex(idx)
            self._dd_check.setChecked(dd)
            self._nb_check.setChecked(nb)

            idx = self._paper_combo.findData(paper)
            if idx >= 0:
                self._paper_combo.setCurrentIndex(idx)
            self._pages_spin.setValue(1)
            self._update_summary()
        else:
            text = (
                f"{pass_count} optimisation pass(es) complete.\n"
                f"Latest profile: {latest_icc}"
            )
        self._status_label.setText(text)
        self._build_profile_btn.setEnabled(False)
        self._generate_addon_btn.setEnabled(True)

    def _latest_icc_path(self) -> Path | None:
        """Return the most recent ICC profile (original or optimised)."""
        if self._optimisation_passes:
            last_pass = self._optimisation_passes[-1]
            if last_pass.get("icc"):
                return Path(str(last_pass["icc"]))
        if self._original_pass.get("icc"):
            return Path(str(self._original_pass["icc"]))
        return None

    def _latest_ti3_path(self) -> Path | None:
        """Return the most recent TI3 (from the latest pass, or original)."""
        if self._optimisation_passes:
            last_pass = self._optimisation_passes[-1]
            if last_pass.get("ti3"):
                return Path(str(last_pass["ti3"]))
        if self._original_pass.get("ti3"):
            return Path(str(self._original_pass["ti3"]))
        return None

    def set_workspace(self, path: Path | str) -> None:
        """Set the directory where add-on chart files are generated.

        The Wizard calls this when the session starts. ``path`` should be
        the SessionManager's temp working directory. Add-on files are
        generated as ``<target_stem>_opt1``, ``<target_stem>_opt2`` etc.
        inside it.
        """
        self.workspace = Path(path)

    def set_target_stem(self, stem: str) -> None:
        """Set the target name stem used for optimisation pass files."""
        self._target_stem = stem

    def _pass_target_stem(self, pass_num: int) -> Path:
        """Return the target stem for optimisation pass *pass_num*."""
        return self.workspace / f"{self._target_stem}_opt{pass_num}"

    # ----------------------------------------------------------- generation

    def _start_add_on_generation(self) -> None:
        if not self._install:
            QMessageBox.warning(self, "ArgyllCMS not found", "ArgyllCMS binaries are not on PATH.")
            return
        if not self._install.xicclu:
            QMessageBox.warning(self, "xicclu not found", "xicclu binary not found on PATH.")
            return

        pass_num = len(self._optimisation_passes) + 1
        precond_icc = self._latest_icc_path()
        if not precond_icc or not precond_icc.exists():
            QMessageBox.warning(self, "Missing profile", "No ICC profile available to precondition against.")
            return

        if self._combined_ti3s:
            pass1_ti3 = self._combined_ti3s[-1]
        else:
            pass1_ti3 = self._original_pass.get("ti3")
        if not pass1_ti3 or not Path(str(pass1_ti3)).exists():
            QMessageBox.warning(self, "Missing measurements", "No pass-1 measurements available.")
            return
        pass1_ti3 = Path(str(pass1_ti3))

        target = self._pass_target_stem(pass_num)

        # Compute target_n from chart settings (mirror GeneratePage)
        instr = self._instr_combo.currentData() or "i1"
        paper = self._paper_combo.currentData() or "A3"
        pages = self._pages_spin.value()
        dd = self._dd_check.isChecked() and instr == "CM"
        nb = self._nb_check.isChecked() and instr == "i1"
        from .generate_page import _PATCHES_PER_PAGE
        ppg = _PATCHES_PER_PAGE.get((instr, paper, dd, nb), 400)
        target_n = ppg * pages

        pool_max = max_selectable_patches()
        if target_n > pool_max:
            QMessageBox.warning(
                self,
                "Too many patches",
                f"The selected patch count ({target_n}) exceeds the maximum "
                f"available candidates ({pool_max}).\n\n"
                f"Please reduce the number of pages or choose a smaller paper size.",
            )
            return
        out_ti1 = target.with_suffix(".ti1")

        self._gen_status.setText("Generating add-on chart…")
        self._go_to_state(self.STATE_GENERATING)
        self._console.clear()
        self._show_console_check.setVisible(True)
        self._show_console_check.setChecked(True)

        printtarg_args = ["-v", f"-i{instr}", "-t300"]
        if dd:
            printtarg_args.append("-h")
        if nb:
            printtarg_args.append("-L")
        if paper == "A3+":
            printtarg_args.extend(["-p483x329", target.name])
        else:
            printtarg_args.extend([f"-p{paper}", target.name])

        self._pending_printtarg_args = printtarg_args
        self._pending_target = target
        self._pending_pass_num = pass_num

        # Progress dialog (modal, no cancel)
        progress = QProgressDialog("Generating optimisation chart...", None, 0, 5, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setAutoClose(True)
        progress.setAutoReset(True)
        progress.setCancelButton(None)
        progress.show()

        try:
            from ...profiling import pass2_generator

            def _on_progress(label: str, current: int, total: int) -> None:
                progress.setLabelText(f"Generating optimisation chart... ({label})")
                progress.setValue(current)

            pass2_generator.generate_pass2_ti1(
                precond_icc=precond_icc,
                pass1_ti3=pass1_ti3,
                out_ti1=out_ti1,
                target_n=target_n,
                xicclu_path=self._install.xicclu,
                progress_callback=_on_progress,
            )
        except Exception as e:
            progress.close()
            self._gen_status.setText(f"Chart generation failed: {e}")
            self._console.append_line(f"Error: {e}")
            self._go_to_state(self.STATE_READY)
            return

        progress.close()

        # Launch printtarg
        work_dir = str(target.parent)
        self._console.append_line(f"cd {work_dir}")
        self._console.append_line(
            f"$ {self._install.printtarg} "
            f"{' '.join(shlex.quote(a) for a in printtarg_args)}"
        )

        self._proc = QProcess(self)
        self._proc.setWorkingDirectory(work_dir)
        self._proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._proc.finished.connect(self._on_printtarg_done)
        self._proc.readyReadStandardOutput.connect(self._on_proc_stdout)
        self._proc.start(str(self._install.printtarg), printtarg_args)

    def _on_proc_stdout(self) -> None:
        if not self._proc:
            return
        data = bytes(self._proc.readAllStandardOutput()).decode("utf-8", "ignore")
        for line in data.splitlines():
            self._console.append_line(line)

    def _on_printtarg_done(self, code: int) -> None:
        self._proc = None
        if code != 0:
            self._gen_status.setText("Chart generation failed (printtarg).")
            return

        target = self._pending_target
        ti2_path = target.with_suffix(".ti2")

        # Store the new pass files
        pass_data = {
            "ti1": target.with_suffix(".ti1"),
            "ti2": ti2_path,
            "ti3": None,
            "icc": None,
        }
        self._optimisation_passes.append(pass_data)

        # Let the wizard know about the newly generated charts (copy,
        # list, show dialog). Must happen before the measurement
        # transition so the modal dialog blocks until user clicks OK.
        self.passChartsGenerated.emit(ti2_path, self._pending_pass_num)

        # Switch to measurement
        self._meas_page.load_ti2(ti2_path)
        self._go_to_state(self.STATE_MEASURE)

    # ----------------------------------------------------------- measurement

    def _on_measurements_complete(self, ti3_path: Path) -> None:
        """Called when the embedded MeasurementPage finishes reading."""
        if not self._optimisation_passes:
            return
        self._optimisation_passes[-1]["ti3"] = ti3_path
        self._build_profile_btn.setEnabled(True)
        self._go_to_state(self.STATE_READY)
        self._update_ready_status()

    # ----------------------------------------------------------- build profile

    def _run_build_profile(self) -> None:
        if not self._install:
            return
        if not self._optimisation_passes:
            return

        pass_num = len(self._optimisation_passes)
        latest_pass = self._optimisation_passes[-1]
        pass_ti3 = latest_pass.get("ti3")
        if not pass_ti3 or not Path(str(pass_ti3)).exists():
            QMessageBox.warning(self, "Missing data", "The add-on chart has not been measured yet.")
            return

        # Collect all TI3 sources: original + every optimisation pass
        sources: list[Path] = []
        orig_ti3 = self._original_pass.get("ti3")
        if orig_ti3:
            sources.append(Path(str(orig_ti3)))
        for p in self._optimisation_passes:
            pt = p.get("ti3")
            if pt:
                sources.append(Path(str(pt)))

        if len(sources) < 2:
            QMessageBox.warning(self, "Missing data", "Not enough measurement files to combine.")
            return

        target = self._pass_target_stem(pass_num)
        combined = target.parent / f"{target.stem}_combined{pass_num}.ti3"

        self._build_status.setText("Combining measurements…")
        self._go_to_state(self.STATE_BUILDING)
        self._console.clear()
        self._show_console_check.setVisible(True)
        self._show_console_check.setChecked(True)

        try:
            ti3_combiner.combine_all(sources, combined)
            self._combined_ti3s.append(combined)
            self._console.append_line(f"Combined {len(sources)} measurement files → {combined}")
        except Exception as e:
            self._build_status.setText(f"Combination failed: {e}")
            self._console.append_line(f"Error: {e}")
            return

        # Build profile
        settings = self._collect_settings_from_ui()
        # Store settings for this pass
        self._profile_configs.append(settings)

        self._build_status.setText("Creating optimised profile…")

        work_dir = str(combined.parent)
        name = combined.stem
        quality = settings.get("quality", "h")
        quality_code = quality[0].lower() if quality else "h"
        args = ["-v", f"-q{quality_code}", "-cmt", "-dpp"]

        smoothing = settings.get("smoothing", 0.5)
        if abs(smoothing - 0.5) > 0.001:
            r_val = f"{smoothing:.10f}".rstrip("0").rstrip(".")
            args.append(f"-r{r_val}")

        desc = settings.get("description", "")
        if desc:
            args.extend(["-D", desc])
        else:
            args.extend(["-D", f"Optimised Profile (Pass {pass_num})"])

        gamut = settings.get("gamut_profile")
        if gamut:
            args.extend(["-S", gamut])
        else:
            # Try to auto-discover
            auto_gamut = _find_clay_rgb()
            if auto_gamut:
                args.extend(["-S", auto_gamut])

        args.append(name)

        self._console.append_line(
            f"$ {self._install.colprof} {' '.join(shlex.quote(a) for a in args)}"
        )

        self._proc = QProcess(self)
        self._proc.setWorkingDirectory(work_dir)
        self._proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._proc.finished.connect(self._on_profile_done)
        self._proc.readyReadStandardOutput.connect(self._on_proc_stdout)
        self._proc.start(str(self._install.colprof), args)

    def _on_profile_done(self, code: int) -> None:
        self._proc = None
        if code != 0:
            self._build_status.setText("Profile creation failed.")
            return

        pass_num = len(self._optimisation_passes)
        target = self._pass_target_stem(pass_num)
        icc_path = target.with_suffix(".icc")

        self._optimisation_passes[-1]["icc"] = icc_path
        self._done_path_label.setText(str(icc_path))
        self._go_to_state(self.STATE_DONE)
        self.optimisationComplete.emit(icc_path)

    # ----------------------------------------------------------- again

    def _on_optimise_again(self) -> None:
        self._update_ready_status()
        self._go_to_state(self.STATE_READY)

    # ----------------------------------------------------------- snapshot

    def snapshot(self) -> dict[str, Any]:
        """Return a serialisable dict representing current state."""
        return {
            "generate_config": self._generate_config,
            "profile_configs": self._profile_configs,
            "optimisation_count": len(self._optimisation_passes),
        }

    def apply_snapshot(self, data: dict[str, Any]) -> None:
        """Restore state from a serialisable dict (used after .wsp load)."""
        self._generate_config = dict(data.get("generate_config", {}))
        self._profile_configs = list(data.get("profile_configs", []))
        # Pass files are restored by load_wsp_session via the file manifest
