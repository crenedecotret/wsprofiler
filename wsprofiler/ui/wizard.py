from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..session_manager import SessionManager
from .pages.generate_page import GeneratePage
from .pages.measurement_page import MeasurementPage
from .pages.optimise_page import OptimisePage
from .pages.profile_page import ProfilePage
from .session_controller import find_tiff_pages


class Wizard(QWidget):
    """Wizard layout with nav list + stacked pages.

    The Wizard owns a :class:`SessionManager` that provides the temp
    working directory and auto-saves a .wsp archive at each major step.
    """

    def __init__(self, workspace: Path, parent=None) -> None:
        super().__init__(parent)
        self.workspace = workspace

        self._session = SessionManager()
        # When True, modal dialogs are skipped (useful in tests and in
        # scenarios where the wizard is driven programmatically). The
        # non-dialog side effects (auto-save, page transitions) still run.
        self._suppress_dialogs: bool = False
        # Wizard navigation state. These MUST be initialised before the
        # steps.currentRowChanged signal is connected below, otherwise the
        # first setCurrentRow() call fires _on_step_changed which reads
        # _prev_step_index and crashes with AttributeError.
        self._prev_step_index: int = 0
        self._measurement_complete: bool = False
        self._pending_ti2: Path | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # --- left sidebar: step list + Load Session button ---------------
        left = QWidget()
        left.setFixedWidth(180)
        left_lyt = QVBoxLayout(left)
        left_lyt.setContentsMargins(0, 0, 0, 0)
        left_lyt.setSpacing(4)

        self.steps = QListWidget()
        self.steps.setAlternatingRowColors(True)
        # Stretch factor 1 makes the list expand to fill all available
        # vertical space; the Load Session button below gets its natural
        # size and stays pinned to the bottom of the sidebar.
        left_lyt.addWidget(self.steps, stretch=1)

        self._load_session_btn = QPushButton("Session Manager")
        self._load_session_btn.setToolTip(
            "Open the session manager to resume or delete saved sessions."
        )
        self._load_session_btn.clicked.connect(self._on_load_session_clicked)
        left_lyt.addWidget(self._load_session_btn)

        layout.addWidget(left)
        # -----------------------------------------------------------------

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, stretch=1)

        self._pages = {
            "Generate": GeneratePage(workspace=workspace),
            "Measure": MeasurementPage(workspace=workspace),
            "Profile": ProfilePage(workspace=workspace),
            "Optimise": OptimisePage(workspace=workspace),
        }

        for title, page in self._pages.items():
            QListWidgetItem(title, self.steps)
            self.stack.addWidget(page)

        self.steps.currentRowChanged.connect(self._on_step_changed)
        self.steps.setCurrentRow(list(self._pages.keys()).index("Generate"))

        # The wizard always has a live session so the working directory
        # is ready before the first Generate click. The user can start
        # a new run by closing the app and re-opening, or by loading a
        # .wsp from the sidebar.
        self._initialise_session()
        self._update_step_availability()

        self._pages["Generate"].chartGenerated.connect(self._on_chart_generated)
        self._pages["Measure"].measurementsComplete.connect(self._on_measurements_complete)
        self._pages["Measure"].measurementStopped.connect(self._on_measurement_stopped)
        self._pages["Profile"].profileGenerated.connect(self._on_profile_generated)
        self._pages["Optimise"].optimisationComplete.connect(self._on_optimisation_complete)
        self._pages["Optimise"].passChartsGenerated.connect(self._on_pass_charts_generated)

    # ------------------------------------------------------------ session
    @property
    def session(self) -> SessionManager:
        return self._session

    def _initialise_session(self) -> None:
        """Create a fresh temp dir and point all pages at it."""
        self._session.create_new()
        self._measurement_complete = False
        self._pending_ti2 = None
        # Capture the user's ICC destination from the GeneratePage so it
        # is available when the profile is eventually created.
        dest = self._pages["Generate"].final_icc_destination
        if dest:
            self._session.set_final_icc_path(dest)
        # Point all pages at the new working directory.
        self._pages["Generate"].set_working_directory(str(self._session.temp_dir))
        self._pages["Optimise"].set_workspace(self._session.temp_dir)
        self._pages["Optimise"].set_target_stem(self._session.target_stem)

    def _on_load_session_clicked(self) -> None:
        """Open the session manager dialog and load the chosen session."""
        from .session_manager_dialog import SessionManagerDialog

        dialog = SessionManagerDialog(
            self._session.default_sessions_dir, parent=self
        )
        dialog.exec()
        if dialog.is_new_session():
            self._pages["Measure"].reset_state()
            self._pages["Profile"].ti3_edit.clear()
            self._pages["Profile"].desc_edit.clear()
            self._pages["Optimise"]._optimisation_passes = []
            self._pages["Optimise"]._combined_ti3s = []
            self._pages["Optimise"]._original_pass = {}
            self._initialise_session()
            self._update_step_availability()
            target_idx = list(self._pages.keys()).index("Generate")
            self.steps.setCurrentRow(target_idx)
            return
        selected = dialog.selected_path()
        if selected:
            self.load_wsp_session(selected)

    def load_wsp_session(self, wsp_path: Path) -> None:
        """Load a .wsp archive and restore page state.

        The WSP is extracted into the SessionManager's temp dir. Each
        page is repopulated with the data it needs. The wizard jumps to
        the appropriate step based on what was completed.
        """
        try:
            manifest, files, temp_dir = self._session.load_from_wsp(wsp_path)
        except Exception as exc:
            QMessageBox.critical(self, "Failed to Load Session", str(exc))
            return

        # Normalize old two-step role keys to the simple-wizard format
        # so sessions created by the old wizard still load correctly.
        _OLD_KEYS = {
            "chart1_ti2": "ti2",
            "chart1_ti3": "ti3",
            "chart1_icc": "icc",
        }
        for old_key, new_key in _OLD_KEYS.items():
            if old_key in files and new_key not in files:
                files[new_key] = files[old_key]

        # Files may exist in the temp dir (from zip extraction) even if the
        # manifest's files dict doesn't reference them (e.g. old corrupted
        # WSPs where ti1/ti2/ti3 were lost from the manifest on a bad save).
        # Scan the temp dir for known working file types and fill in any
        # missing roles so the pages can still be restored.
        if self._session.target_path is not None:
            for role, suffix in (
                ("ti1", ".ti1"),
                ("ti2", ".ti2"),
                ("ti3", ".ti3"),
            ):
                if role not in files:
                    candidate = self._session.target_path.with_suffix(suffix)
                    if candidate.exists():
                        files[role] = candidate

        # Reset all pages before restoring state from the new session.
        self._pages["Measure"].reset_state()
        self._pages["Profile"].ti3_edit.clear()
        self._pages["Profile"].desc_edit.clear()
        self._pages["Optimise"]._optimisation_passes = []
        self._pages["Optimise"]._combined_ti3s = []
        self._pages["Optimise"]._original_pass = {}
        self._pending_ti2 = None
        self._measurement_complete = manifest.get("measurement_complete", False)

        # Make sure every page knows the working directory.
        gen_page = self._pages["Generate"]
        prof_page = self._pages["Profile"]
        opt_page = self._pages["Optimise"]

        gen_page.set_working_directory(temp_dir)
        opt_page.set_workspace(temp_dir)

        # Restore GeneratePage state (instrument, paper, destination, etc.).
        # The preconditioning profile is deliberately NOT restored to the
        # Generate UI: the chart has already been generated by the time a
        # WSP is loaded, so there is nothing to precondition. The precond
        # ICC bundled in the archive (role "precond_icc") is kept purely for
        # reference/traceability. If present, log it for the user.
        gen_config = manifest.get("generate_config", {})
        gen_page.set_automatic_config(
            device=gen_config.get("device"),
            paper=gen_config.get("paper"),
            precond_path=None,
            target_path=gen_config.get("target_path"),
        )
        precond_ref = gen_config.get("precond_path") or files.get("precond_icc")
        if precond_ref:
            self._pages["Measure"].console.append_line(
                f"Preconditioning profile used for this session: {precond_ref}"
            )

        # Restore the final ICC destination from the generate_config.
        target_path = gen_config.get("target_path")
        if target_path:
            self._session.set_final_icc_path(Path(target_path))
        opt_page.set_target_stem(self._session.target_stem)

        # Restore MeasurementPage state.
        ti2_path = files.get("ti2")
        if ti2_path is not None and Path(ti2_path).exists():
            self._pending_ti2 = Path(ti2_path)
        else:
            self._pending_ti2 = None

        # Restore ProfilePage state.
        ti3_path = files.get("ti3")
        if ti3_path is not None and Path(ti3_path).exists():
            icc_dest = gen_config.get("target_path")
            profile_name = Path(icc_dest).stem if icc_dest else ""
            prof_page.set_ti3_path(Path(ti3_path), profile_name=profile_name)

        profile_configs = manifest.get("profile_configs", [])
        if profile_configs:
            last_config = profile_configs[-1]
            prof_page.set_automatic_config(
                ti3_path=ti3_path,
                quality=last_config.get("quality"),
                smoothing=last_config.get("smoothing"),
                gamut_path=last_config.get("gamut_profile"),
                clear_gamut=not last_config.get("gamut_profile"),
                description=last_config.get("description"),
            )

        # Restore OptimisePage state.
        if manifest.get("optimisation_count", 0) > 0 or files.get("icc") is not None:
            opt_files = {
                "ti1": files.get("ti1"),
                "ti2": files.get("ti2"),
                "ti3": files.get("ti3"),
                "icc": files.get("icc"),
            }
            opt_page.set_original_pass_data(
                generate_config=gen_config,
                profile_config=profile_configs[-1] if profile_configs else {},
                files={k: v for k, v in opt_files.items() if v is not None},
            )
            # Also restore optimisation passes from the manifest
            opt_page._generate_config = dict(gen_config)
            opt_page._profile_configs = list(profile_configs)
            opt_page._optimisation_passes = []
            opt_page._combined_ti3s = []
            for i in range(1, manifest.get("optimisation_count", 0) + 1):
                opt_page._optimisation_passes.append({
                    "ti1": files.get(f"pass{i}_ti1"),
                    "ti2": files.get(f"pass{i}_ti2"),
                    "ti3": files.get(f"pass{i}_ti3"),
                    "icc": files.get(f"pass{i}_icc"),
                })
                combined = files.get(f"combined{i}_ti3")
                if combined:
                    opt_page._combined_ti3s.append(combined)

        # Decide which step to show.
        has_icc = files.get("icc") is not None and Path(str(files["icc"])).exists()
        has_ti3 = ti3_path is not None and Path(str(ti3_path)).exists()
        has_ti2 = ti2_path is not None and Path(str(ti2_path)).exists()
        meas_complete = manifest.get("measurement_complete", False)

        if has_icc:
            target_step = "Optimise"
        elif has_ti3 and meas_complete:
            target_step = "Profile"
        elif has_ti2:
            target_step = "Measure"
        else:
            target_step = "Generate"
        target_index = list(self._pages.keys()).index(target_step)
        self._update_step_availability()
        self.steps.setCurrentRow(target_index)
        self._refresh_page_for_step(target_index)

    # ---------------------------------------------------------- callbacks
    def _on_chart_generated(self, ti2_path: Path) -> None:
        # Sync the session with the destination the user actually chose
        # for this generation (may differ from the startup default).
        dest = self._pages["Generate"].generated_target_path
        if dest:
            self._session.set_final_icc_path(dest)

        # Auto-save the WSP after the chart is generated.
        self._auto_save_after_generate(ti2_path)

        # Show print instructions dialog (skipped in test mode).
        if not self._suppress_dialogs:
            self._show_print_dialog(ti2_path)

        # Switch to measurement page after user clicks OK
        self._pending_ti2 = ti2_path
        self._update_step_availability()
        measure_idx = list(self._pages.keys()).index("Measure")
        self.steps.setCurrentRow(measure_idx)

    def _show_print_dialog(
        self, ti2_path: Path, title: str = "Charts Ready for Printing"
    ) -> None:
        """Copy the TIFF chart pages to the ICC destination folder and
        show a rich-text dialog listing every file that must be printed."""
        tiff_files = list(find_tiff_pages(ti2_path.with_suffix("")))

        dest = self._session.final_icc_path
        if dest is not None:
            dest_dir = dest.parent
            dest_dir.mkdir(parents=True, exist_ok=True)
            new_stem = dest.stem
            copied: list[Path] = []
            for i, src in enumerate(tiff_files, start=1):
                dst_name = f"{new_stem}.tif" if len(tiff_files) == 1 else f"{new_stem}_{i}.tif"
                dst = dest_dir / dst_name
                shutil.copy2(src, dst)
                copied.append(dst)
            tiff_files = copied
            folder = dest_dir
        else:
            folder = ti2_path.parent

        if not tiff_files:
            QMessageBox.information(self, title, f"No TIFF files found in {folder}")
            return

        file_lines = [
            f'<span style="color:#D4A76A;">{f.name}</span>' for f in tiff_files
        ]
        file_list_html = "<br>".join(file_lines)

        msg = QMessageBox(self)
        msg.setWindowTitle(title)
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setTextFormat(Qt.TextFormat.RichText)
        msg.setText(
            "The chart files have been saved to:<br><br>"
            f'<span style="color:#e0e0e8;">{folder}</span><br><br>'
            f"{file_list_html}<br><br>"
            "Please print the TIFF files above at their original size with "
            "color management DISABLED in your printer driver.<br><br>"
            "Important:<br>"
            "• Write down your printer settings (paper type, quality, etc.)<br>"
            "• The generated ICC profile will be unique to these settings<br>"
            "• Allow prints to dry for at least 1 hour before measuring"
        )
        msg.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg.exec()

    def _on_pass_charts_generated(self, ti2_path: Path, pass_num: int) -> None:
        """Show the print dialog for an optimisation pass chart."""
        if not self._suppress_dialogs:
            self._show_print_dialog(
                ti2_path, title=f"Optimisation Pass {pass_num} — Charts Ready for Printing"
            )

    def _on_measurements_complete(self, ti3_path: Path) -> None:
        """Show dialog when all measurements are done, offer to go to Profile page."""
        self._measurement_complete = True
        # Auto-save the WSP now that ti3 exists.
        self._auto_save_after_measure(ti3_path)

        proceed_to_profile = True
        if not self._suppress_dialogs:
            msg = QMessageBox(self)
            msg.setWindowTitle("Measurements Complete")
            msg.setIcon(QMessageBox.Icon.Information)
            msg.setText(
                "All chart measurements are complete!\n\n"
                f"Measurement data saved to:\n{ti3_path}\n\n"
                "You are now ready to create the ICC profile."
            )
            msg.setStandardButtons(
                QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Ok
            )
            msg.button(QMessageBox.StandardButton.Cancel).setText("Return to measurements")
            msg.button(QMessageBox.StandardButton.Ok).setText("Create Profile")

            result = msg.exec()
            proceed_to_profile = result == QMessageBox.StandardButton.Ok

        if proceed_to_profile:
            self._update_step_availability()
            profile_idx = list(self._pages.keys()).index("Profile")
            self.steps.setCurrentRow(profile_idx)

    def _on_measurement_stopped(self) -> None:
        """Auto-save WSP whenever chartread stops (partial or full)."""
        self.save_session_state()

    def _on_profile_generated(self, icc_path: Path) -> None:
        """Copy final ICC, feed data to Optimise page, auto-save WSP."""
        # Copy the ICC to the final destination.
        final_dest = self._session.copy_final_icc(icc_path)
        if final_dest is not None and not self._suppress_dialogs:
            self._show_info(
                "Profile Created",
                f"ICC profile saved to:\n{final_dest}",
            )

        gen_page = self._pages["Generate"]
        prof_page = self._pages["Profile"]
        opt_page = self._pages["Optimise"]

        # Build the file manifest. Derive the ti3 path from the
        # workspace (target stem is always "session" — see
        # GeneratePage._on_generate) so that ti1/ti2/ti3/tiff files
        # are always included regardless of MeasurementPage runtime
        # state.
        target = self._session.target_path
        ti3_path = target.with_suffix(".ti3") if target else None
        file_paths = self._collect_session_files(include_icc=icc_path, ti3_path=ti3_path)

        generate_config = gen_page.get_generate_config()
        profile_config = prof_page.get_profile_config()

        # Feed data to the Optimise page so it can run immediately
        opt_page.set_original_pass_data(
            generate_config=generate_config,
            profile_config=profile_config,
            files=file_paths,
        )

        # Auto-save the WSP.
        self._session.auto_save(
            file_paths=file_paths,
            generate_config=generate_config,
            profile_configs=[profile_config],
            optimisation_count=0,
            measurement_complete=True,
        )
        self._update_step_availability()

    def _on_optimisation_complete(self, icc_path: Path) -> None:
        """Copy final ICC and auto-save the updated WSP."""
        final_dest = self._session.copy_final_icc(icc_path)
        if final_dest is not None and not self._suppress_dialogs:
            self._show_info(
                "Optimised Profile Created",
                f"Optimised ICC profile saved to:\n{final_dest}",
            )

        opt_page = self._pages["Optimise"]
        # Build the file manifest including all optimisation pass files
        file_paths = self._collect_optimisation_files(opt_page)

        # Auto-save the WSP with all optimisation pass data
        self._session.auto_save(
            file_paths=file_paths,
            generate_config=opt_page._generate_config,
            profile_configs=opt_page._profile_configs,
            optimisation_count=len(opt_page._optimisation_passes),
            measurement_complete=True,
        )

    def save_session_state(self) -> None:
        """Snapshot the current session state and auto-save the WSP.

        Unlike the old implementation (which hard-coded an empty
        ``profile_configs`` and ``optimisation_count=0`` and never
        collected the ICC), this captures the live state of the wizard:
        optimisation files take precedence, then the post-profile files
        (with ICC), then the pre-profile workspace files. This way the
        save triggered on app close, chartread exit, or navigating away
        from the Measure page no longer wipes completed profile and
        optimisation data from the archive.
        """
        gen_page = self._pages["Generate"]
        prof_page = self._pages["Profile"]
        opt_page = self._pages["Optimise"]

        if opt_page.has_data():
            file_paths = self._collect_optimisation_files(opt_page)
            profile_configs = list(opt_page._profile_configs)
            optimisation_count = len(opt_page._optimisation_passes)
        else:
            # A profile may have been produced without the Optimise page
            # being wired up yet. Include the ICC if it is on disk.
            target = self._session.target_path
            icc_path = target.with_suffix(".icc") if target is not None else None
            if icc_path is not None and icc_path.exists():
                ti3_path = icc_path.with_suffix(".ti3")
                file_paths = self._collect_session_files(include_icc=icc_path, ti3_path=ti3_path)
                profile_configs = [prof_page.get_profile_config()]
                optimisation_count = 0
            else:
                file_paths = self._collect_workspace_files()
                profile_configs = []
                optimisation_count = 0

        # Always include the preconditioning ICC the user picked on the
        # Generate page, if any, so the WSP is self-contained.
        self._maybe_add_precond_icc(file_paths, gen_page.get_generate_config())

        if not file_paths:
            return

        self._session.auto_save(
            file_paths=file_paths,
            generate_config=gen_page.get_generate_config(),
            profile_configs=profile_configs,
            optimisation_count=optimisation_count,
            measurement_complete=self._measurement_complete,
        )

    def _collect_workspace_files(self) -> dict[str, Path]:
        """Collect files from the temp dir using the target stem."""
        stem_path = self._session.target_path
        if stem_path is None:
            return {}
        file_paths: dict[str, Path] = {}
        ti1 = stem_path.with_suffix(".ti1")
        if ti1.exists():
            file_paths["ti1"] = ti1
        ti2 = stem_path.with_suffix(".ti2")
        if ti2.exists():
            file_paths["ti2"] = ti2
        ti3 = stem_path.with_suffix(".ti3")
        if ti3.exists():
            file_paths["ti3"] = ti3
        for idx, tiff in enumerate(find_tiff_pages(stem_path), start=1):
            file_paths[f"tiff_{idx}"] = tiff
        return file_paths

    @staticmethod
    def _maybe_add_precond_icc(
        file_paths: dict[str, Path], generate_config: dict
    ) -> None:
        """Bundle the preconditioning ICC into ``file_paths`` for reference.

        The precond ICC lives outside the temp dir (usually a system
        profile), so ``save_session`` stores it under a flat arcname
        (the file's basename). The Generate UI is not restored on load
        because the chart has already been generated by then.
        """
        precond = generate_config.get("precond_path")
        if precond:
            p = Path(precond)
            if p.exists() and "precond_icc" not in file_paths:
                file_paths["precond_icc"] = p

    # ----------------------------------------------------------- helpers
    def _show_info(self, title: str, text: str) -> None:
        msg = QMessageBox(self)
        msg.setWindowTitle(title)
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setText(text)
        msg.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg.exec()

    def _auto_save_after_generate(self, ti2_path: Path) -> None:
        """Collect generate-step files and auto-save the WSP."""
        work_dir = ti2_path.parent
        stem = ti2_path.stem
        file_paths: dict[str, Path] = {}

        ti1 = work_dir / f"{stem}.ti1"
        if ti1.exists():
            file_paths["ti1"] = ti1
        if ti2_path.exists():
            file_paths["ti2"] = ti2_path
        tiff_stem = work_dir / stem
        for idx, tiff in enumerate(find_tiff_pages(tiff_stem), start=1):
            file_paths[f"tiff_{idx}"] = tiff

        gen_page = self._pages["Generate"]
        gen_config = gen_page.get_generate_config()
        self._maybe_add_precond_icc(file_paths, gen_config)
        self._session.auto_save(
            file_paths=file_paths,
            generate_config=gen_config,
            profile_configs=[],
            optimisation_count=0,
        )

    def _auto_save_after_measure(self, ti3_path: Path) -> None:
        """Collect generate+measure files and auto-save the WSP."""
        work_dir = ti3_path.parent
        stem = ti3_path.stem
        file_paths: dict[str, Path] = {}

        ti1 = work_dir / f"{stem}.ti1"
        if ti1.exists():
            file_paths["ti1"] = ti1
        ti2 = work_dir / f"{stem}.ti2"
        if ti2.exists():
            file_paths["ti2"] = ti2
        if ti3_path.exists():
            file_paths["ti3"] = ti3_path
        tiff_stem = work_dir / stem
        for idx, tiff in enumerate(find_tiff_pages(tiff_stem), start=1):
            file_paths[f"tiff_{idx}"] = tiff

        gen_page = self._pages["Generate"]
        gen_config = gen_page.get_generate_config()
        self._maybe_add_precond_icc(file_paths, gen_config)
        self._session.auto_save(
            file_paths=file_paths,
            generate_config=gen_config,
            profile_configs=[],
            optimisation_count=0,
            measurement_complete=True,
        )

    def _collect_session_files(self, include_icc: Path | None = None, ti3_path: Path | None = None) -> dict[str, Path]:
        """Collect all current session files with their roles."""
        file_paths: dict[str, Path] = {}

        if ti3_path is None:
            ti3_path = self._pages["Measure"].get_current_ti3_path()

        if ti3_path is None:
            target = self._session.target_path
            if target is not None:
                candidate = target.with_suffix(".ti3")
                if candidate.exists():
                    ti3_path = candidate

        if ti3_path is not None:
            work_dir = ti3_path.parent
            stem = ti3_path.stem
            ti1 = work_dir / f"{stem}.ti1"
            if ti1.exists():
                file_paths["ti1"] = ti1
            ti2 = work_dir / f"{stem}.ti2"
            if ti2.exists():
                file_paths["ti2"] = ti2
            if ti3_path.exists():
                file_paths["ti3"] = ti3_path
            tiff_stem = work_dir / stem
            for idx, tiff in enumerate(find_tiff_pages(tiff_stem), start=1):
                file_paths[f"tiff_{idx}"] = tiff

        if include_icc is not None and include_icc.exists():
            file_paths["icc"] = include_icc

        self._maybe_add_precond_icc(file_paths, self._pages["Generate"].get_generate_config())
        return file_paths

    def _collect_optimisation_files(self, opt_page) -> dict[str, Path]:
        """Collect original + all optimisation pass files."""
        file_paths: dict[str, Path] = {}

        # Original pass
        orig = opt_page._original_pass
        for key in ("ti1", "ti2", "ti3", "icc"):
            p = orig.get(key)
            if p and Path(str(p)).exists():
                file_paths[key] = Path(str(p))

        # Original TIFF pages
        target = self._session.target_path
        if target is not None:
            for idx, tiff in enumerate(find_tiff_pages(target), start=1):
                if f"tiff_{idx}" not in file_paths:
                    file_paths[f"tiff_{idx}"] = tiff

        # Optimisation pass files
        for i, pass_data in enumerate(opt_page._optimisation_passes, start=1):
            for key in ("ti1", "ti2", "ti3", "icc"):
                p = pass_data.get(key)
                if p and Path(str(p)).exists():
                    file_paths[f"pass{i}_{key}"] = Path(str(p))
            pass_target = opt_page._pass_target_stem(i)
            for idx, tiff in enumerate(find_tiff_pages(pass_target), start=1):
                file_paths[f"pass{i}_tiff_{idx}"] = tiff

        # Combined TI3 files
        for i, combined in enumerate(opt_page._combined_ti3s, start=1):
            if combined.exists():
                file_paths[f"combined{i}_ti3"] = combined

        # Bundle the preconditioning ICC used for the original pass, if any,
        # so the saved session is self-contained.
        self._maybe_add_precond_icc(file_paths, opt_page._generate_config or {})

        return file_paths

    def _refresh_page_for_step(self, index: int) -> None:
        """Apply page-specific state (chart loading, ti3 path) for the given step."""
        page = self.stack.widget(index)
        if page is self._pages["Measure"] and self._pending_ti2 is not None:
            if self._pending_ti2.exists():
                page.load_ti2(self._pending_ti2)
            self._pending_ti2 = None
        page = self.stack.widget(index)
        if page is self._pages["Profile"]:
            ti3_path = self._pages["Measure"].get_current_ti3_path()
            if ti3_path:
                icc_dest = self._pages["Generate"].final_icc_destination
                profile_name = icc_dest.stem if icc_dest else ""
                page.set_ti3_path(ti3_path, profile_name=profile_name)

    def _on_step_changed(self, index: int) -> None:
        if self._prev_step_index == 1:
            self.save_session_state()
        self._prev_step_index = index
        self.stack.setCurrentIndex(index)
        self._refresh_page_for_step(index)

    def _set_item_enabled(self, item: QListWidgetItem, enabled: bool) -> None:
        flags = item.flags()
        if enabled:
            item.setFlags(flags | Qt.ItemIsEnabled)
        else:
            item.setFlags(flags & ~Qt.ItemIsEnabled)

    def _update_step_availability(self) -> None:
        """Enable sidebar steps based on completion state."""
        titles = list(self._pages.keys())
        items = [self.steps.item(i) for i in range(self.steps.count())]

        # Generate is always available
        self._set_item_enabled(items[0], True)
        items[0].setToolTip("")

        # Measure available if ti2 exists
        measure_available = self._pending_ti2 is not None
        if not measure_available:
            target = self._session.target_path
            if target is not None:
                measure_available = target.with_suffix(".ti2").exists()
        self._set_item_enabled(
            items[1], measure_available
        )
        items[1].setToolTip(
            "" if measure_available else "Generate a chart first"
        )

        # Profile available if ti3 exists
        profile_available = self._pages["Measure"].get_current_ti3_path() is not None
        if not profile_available:
            target = self._session.target_path
            if target is not None:
                profile_available = target.with_suffix(".ti3").exists()
        self._set_item_enabled(
            items[2], profile_available
        )
        items[2].setToolTip(
            "" if profile_available else "Complete measurements first"
        )

        # Optimise available if icc exists or OptimisePage has data
        optimise_available = False
        target = self._session.target_path
        if target is not None:
            optimise_available = target.with_suffix(".icc").exists()
        if not optimise_available:
            optimise_available = self._pages["Optimise"].has_data()
        self._set_item_enabled(
            items[3], optimise_available
        )
        items[3].setToolTip(
            "" if optimise_available else "Create a profile first"
        )
