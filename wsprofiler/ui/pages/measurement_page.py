from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...argyll import chartread, discover
from ...ti import ti2, ti3
from ..chart_view import ChartView
from ..chartread_dialog import show_calibration_dialog, show_confirm_dialog, show_error_dialog
from ..log_console import LogConsole
from ..ti3_watcher import TI3Watcher, _simple_xyz_to_rgb


class MeasurementPage(QWidget):
    """UI for reading color charts with a spectrophotometer."""
    
    measurementsComplete = Signal(Path)  # Emitted when all rows are read
    
    def __init__(self, workspace: Path, parent=None) -> None:
        super().__init__(parent)
        self.workspace = workspace
        self._patches = []

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(12, 12, 12, 12)
        root_layout.setSpacing(8)

        main = QWidget()
        main_layout = QVBoxLayout(main)
        main_layout.setSpacing(12)

        header_layout = QHBoxLayout()
        self.title_label = QLabel("Measurement")
        self.title_label.setStyleSheet("font-size: 20px; font-weight: 600;")
        header_layout.addWidget(self.title_label)
        header_layout.addStretch()
        main_layout.addLayout(header_layout)

        self.status_label = QLabel("Load a chart to begin.")
        main_layout.addWidget(self.status_label)

        self.chart_view = ChartView()
        self.chart_view.setMinimumSize(600, 500)
        self.chart_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        main_layout.addWidget(self.chart_view, stretch=1)

        button_row = QHBoxLayout()
        self.load_button = QPushButton("Load Chart")
        self.start_button = QPushButton("Read Chart")
        self.start_button.setEnabled(False)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setVisible(False)
        button_row.addWidget(self.load_button)
        button_row.addWidget(self.start_button)
        button_row.addWidget(self.stop_button)

        # Error response buttons (hidden by default)
        self.accept_btn = QPushButton("Accept (Enter)")
        self.retry_btn = QPushButton("Retry")
        self.giveup_btn = QPushButton("Give Up (q)")
        self.accept_btn.setVisible(False)
        self.retry_btn.setVisible(False)
        self.giveup_btn.setVisible(False)
        button_row.addWidget(self.accept_btn)
        button_row.addWidget(self.retry_btn)
        button_row.addWidget(self.giveup_btn)

        button_row.addStretch()
        main_layout.addLayout(button_row)

        self.console = LogConsole()
        self.console.setFixedHeight(160)
        main_layout.addWidget(self.console)

        root_layout.addWidget(main, stretch=1)

        self.load_button.clicked.connect(self._on_load)
        self.start_button.clicked.connect(lambda: self._on_start())
        self.stop_button.clicked.connect(self._on_stop)
        self.accept_btn.clicked.connect(self._on_accept_reading)
        self.retry_btn.clicked.connect(self._on_retry_reading)
        self.giveup_btn.clicked.connect(self._on_giveup_reading)
        
        # Connect strip click for navigation during measurement
        self.chart_view.stripClicked.connect(self._on_strip_clicked)

        self._session: chartread.ChartReadSession | None = None
        self._current_chart_path: Path | None = None
        self._install = discover.discover()
        self._current_strip: int | None = None  # Track current strip during measurement
        self._target_strip: int | None = None  # Pending navigation target
        # Auto save/restart cycle: after each strip read or on navigation click,
        # we stop chartread (which writes the .ti3) and re-launch with -r -N to
        # both refresh the split view and avoid the "Trigger instrument switch"
        # state machine eating keystrokes.
        self._pending_restart: bool = False
        self._initial_strip_seen: bool = False  # ignore first "Ready" of a session
        self._dialog_active: bool = False  # modal prompt dialog is open
        self._retry_pending: bool = False  # auto-retry key already sent; waiting for chartread to recover
        self._auto_done_sent: bool = False  # 'd' already sent in response to ALL ROWS READ
        
        # TI3 watcher for split-patch display during measurement
        self._ti3_watcher = TI3Watcher(self)
        self._ti3_watcher.colorsUpdated.connect(self._on_measured_colors_updated)

        self._load_sample()


    def _load_sample(self) -> None:
        sample = self.workspace / "assets" / "sample" / "sample_chart.ti2"
        if sample.exists():
            self._load_file(sample)

    def _on_load(self) -> None:
        settings = QSettings("wsprofiler", "wsprofiler")
        last_dir = settings.value("last_ti2_dir", str(self.workspace))
        
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select printtarg .ti2",
            last_dir,
            "Argyll chart (*.ti2);;All files (*)",
        )
        if path:
            # Save the directory for next time
            settings.setValue("last_ti2_dir", str(Path(path).parent))
            self._load_file(Path(path))

    def get_current_ti3_path(self) -> Path | None:
        """Return the path to the current chart's .ti3 file if it exists."""
        if self._current_chart_path:
            ti3 = self._current_chart_path.with_suffix(".ti3")
            if ti3.exists():
                return ti3
        return None

    def load_ti2(self, path: Path) -> None:
        """Public entry-point to load a .ti2 chart file."""
        self._load_file(path)

    def _load_file(self, path: Path) -> None:
        try:
            patches = ti2.load_patches(path)
        except Exception as exc:  # noqa: BLE001
            self.console.append_line(f"Failed to parse {path.name}: {exc}")
            return

        self._patches = patches
        # Calculate strips per page for layout (26 strips = A-Z = 1 page)
        strips_per_page = 26
        self.chart_view.set_patches(patches, strips_per_page=strips_per_page)
        
        self.status_label.setText(f"Loaded {len(patches)} patches from {path.name}")
        self.console.append_line(f"Loaded {path}")
        self._current_chart_path = path
        self.start_button.setEnabled(self._install is not None)
        
        # Check for existing ti3 file and show measured colors if available
        ti3_path = path.with_suffix('.ti3')
        self.console.append_line(f"Looking for ti3: {ti3_path}")
        self.console.append_line(f"ti3 exists: {ti3_path.exists()}")
        if ti3_path.exists():
            self._load_existing_ti3(ti3_path)
        else:
            self.chart_view.set_measurement_mode(False)

    def _load_existing_ti3(self, ti3_path: Path) -> None:
        """Load measured colors from existing ti3 file and show split view."""
        try:
            measured = ti3.load_measured_patches(ti3_path)
            self.console.append_line(f"Loaded {len(measured)} patches from ti3")
            color_dict: dict[str, tuple[int, int, int]] = {}
            
            for patch in measured:
                if patch.xyz:
                    rgb = _simple_xyz_to_rgb(patch.xyz)
                    color_dict[patch.sample_loc] = rgb
            
            self.console.append_line(f"Colors extracted: {len(color_dict)}")
            if color_dict:
                self.console.append_line(f"Found existing measurements: {ti3_path.name}")
                self.chart_view.set_measurement_mode(True)
                self.chart_view.update_measured_colors(color_dict)
                # Mark each strip with at least one measured patch as read.
                self.chart_view.reset_measured_marks()
                measured_strips = {
                    p.strip for p in measured if p.xyz and p.strip
                }
                for s in measured_strips:
                    self.chart_view.mark_strip_measured(s)
                # Signal that measurements are already complete
                self._current_ti3_path = ti3_path
                self.measurementsComplete.emit(ti3_path)
            else:
                self.console.append_line("No colors with XYZ data found")
                self.chart_view.set_measurement_mode(False)
        except Exception as exc:
            self.console.append_line(f"Note: Could not load {ti3_path.name}: {exc}")
            import traceback
            self.console.append_line(traceback.format_exc())
            self.chart_view.set_measurement_mode(False)

    def _on_start(self, no_calibrate: bool = False) -> None:
        if not self._install or not self._current_chart_path:
            self.console.append_line("Cannot start: missing Argyll binaries or chart file")
            return

        if self._session:
            self.console.append_line("Session already running")
            return

        # Check if TI3 exists to resume
        ti3_path = self._current_chart_path.with_suffix('.ti3')
        resume = ti3_path.exists()
        if resume:
            self.console.append_line(f"Resuming existing readings from {ti3_path.name}")
        if no_calibrate:
            self.console.append_line("Restarting chartread without re-calibration (-N)")

        session = chartread.ChartReadSession(
            chart_path=self._current_chart_path,
            executable=self._install.chartread,
            resume=resume,
            no_calibrate=no_calibrate,
            parent=self,
        )
        session.stdoutLine.connect(self.console.append_line)
        session.stdoutLine.connect(self._on_stdout_line)
        session.stderrLine.connect(self.console.append_line)
        session.statusChanged.connect(self._on_status_changed)
        session.waitingForInput.connect(self._on_waiting_for_input)
        session.runner.finished.connect(self._on_finished)
        self._session = session
        
        # Enable split-patch measurement mode
        self.chart_view.set_measurement_mode(True)
        
        # Start watching TI3 file (created by chartread)
        if self._current_chart_path:
            ti3_path = self._current_chart_path.with_suffix('.ti3')
            self._ti3_watcher.start_watching(ti3_path)
        
        # Toggle button visibility
        self.start_button.setVisible(False)
        self.load_button.setVisible(False)
        self.stop_button.setText("Stop")
        self.stop_button.setStyleSheet("")
        self.stop_button.setVisible(True)
        
        self.console.append_line("Starting chartread…")
        session.start()

    def _on_status_changed(self, status: chartread.MeasurementStatus) -> None:
        if status.strip is not None:
            prev_strip = self._current_strip
            self._current_strip = status.strip
            # While navigating, suppress intermediate-strip highlights so the
            # UI shows a single hop from origin to target.
            if self._target_strip is None or status.strip == self._target_strip:
                self.chart_view.highlight_strip(status.strip)

            if not self._initial_strip_seen:
                self._initial_strip_seen = True
            self._retry_pending = False  # chartread responded with a strip → retry resolved
            if self._target_strip is not None:
                self._step_toward_target()
            elif (
                prev_strip is not None
                and status.strip != prev_strip
                and not self._dialog_active
            ):
                # No nav target → previous strip was just successfully measured.
                self.chart_view.mark_strip_measured(prev_strip)
        if status.message:
            self.status_label.setText(status.message)

    def _on_stdout_line(self, line: str) -> None:
        """Catch end-of-strip signals not covered by status transitions.

        When the very last strip is read chartread does NOT advance to a new
        strip name, so the prev != current heuristic in _on_status_changed
        never fires. We mark the current strip on 'Strip read OK' instead.
        On 'ALL ROWS READ' we relabel the Stop button to 'Done' so the user
        knows the chart is fully read - but we don't auto-finish, so they
        can still navigate back and re-read any strip before saving.
        """
        if "Strip read OK" in line and self._current_strip is not None:
            self.chart_view.mark_strip_measured(self._current_strip)
        if "ALL ROWS READ" in line and self._session:
            # Don't auto-finish: the user may still want to re-read a strip
            # before saving. Just relabel Stop -> Done so they know they can
            # save now, and update the status text.
            self.stop_button.setText("Done (save .ti3)")
            self.stop_button.setStyleSheet("font-weight: bold; color: #1F8A4C;")
            self.status_label.setText(
                "All rows read - click Done to save, or click any strip to re-read it."
            )

    def _step_toward_target(self) -> None:
        """Send one navigation key toward _target_strip; clears target when reached."""
        if not self._session or self._target_strip is None or self._current_strip is None:
            return
        if self._current_strip == self._target_strip:
            self._target_strip = None
            return
        self._session.send_key("f" if self._target_strip > self._current_strip else "b")

    @staticmethod
    def _strip_label(strip: int) -> str:
        label = ""
        n = strip
        while n > 0:
            n -= 1
            label = chr(ord("A") + (n % 26)) + label
            n //= 26
        return label or "A"

    def _on_stop(self) -> None:
        """Stop the running chartread session."""
        if self._session:
            self.console.append_line("Stopping chartread…")
            self._session.cancel()

    def _on_strip_clicked(self, strip: int) -> None:
        """Handle user clicking on a strip to navigate during measurement."""
        if not self._session or self._current_strip is None:
            return
        if strip == self._current_strip:
            return
        offset = strip - self._current_strip
        direction = "forward" if offset > 0 else "backward"
        self.console.append_line(
            f"Navigating {direction} to strip {self._strip_label(strip)}"
        )
        # Stepwise: send one key now; each chartread status response triggers
        # the next key via _step_toward_target() until the target is reached.
        # chartread only reads one navigation key per measurement cycle so
        # bursting all keys at once causes extras to be silently dropped.
        self._target_strip = strip
        self._step_toward_target()

    def _on_waiting_for_input(self, prompt: str) -> None:
        """Handle prompts from chartread (errors, confirmations)."""
        if self._dialog_active:
            # Don't open a second dialog on top of the first.
            self.console.append_line(f"[suppressed prompt while dialog open] {prompt}")
            return
        self._dialog_active = True
        try:
            self._dispatch_prompt(prompt)
        finally:
            self._dialog_active = False

    def _dispatch_prompt(self, prompt: str) -> None:
        if prompt.startswith("ERROR_ACCEPT:"):
            # "Hit Return to use it anyway, any other key to retry, 'q' to give up"
            # The user may be confident a high-DeltaE reading is legitimate
            # (dark patch, saturated color, etc.) so give them the choice.
            prompt_text = prompt[13:]
            self._target_strip = None  # cancel any pending navigation
            self.status_label.setText("[Reading Error - Accept or Retry?]")
            self.console.append_line(f">>> {prompt_text}")
            QApplication.beep()
            result = show_error_dialog(prompt_text, has_accept=True, parent=self)
            if self._session:
                if result == "accept":
                    self._session.send_key("\n")  # Return = accept
                elif result == "giveup":
                    self._session.send_key("q")
                else:  # retry or dialog closed
                    self._retry_pending = True
                    self._session.send_key(" ")
        elif prompt.startswith("ERROR_RETRY:"):
            # "Hit Esc or 'q' to give up, any other key to retry:"
            # chartread didn't even get a usable reading - no accept option.
            prompt_text = prompt[12:]
            if self._retry_pending:
                self.console.append_line(f"[retry already pending, ignoring] {prompt_text}")
                return
            self._retry_pending = True
            self._target_strip = None
            self.status_label.setText("[Reading Error - retrying…]")
            self.console.append_line(f"[auto-retry] {prompt_text}")
            QApplication.beep()
            if self._session:
                self._session.send_key(" ")
        elif prompt.startswith("CONFIRM:"):
            prompt_text = prompt[8:]
            self.status_label.setText("[Confirm]")
            self.console.append_line(f"\n>>> {prompt_text}")
            result = show_confirm_dialog(prompt_text, parent=self)
            if self._session:
                if result == "yes":
                    self._session.send_key("y")
                elif result == "no":
                    self._session.send_key("n")
        elif prompt.startswith("CALIBRATE:"):
            prompt_text = prompt[10:]
            self.status_label.setText("[CALIBRATION REQUIRED]")
            self.console.append_line(f"\n>>> CALIBRATION: {prompt_text}")
            result = show_calibration_dialog(prompt_text, self)
            if result == "enter" and self._session:
                self._session.send_key("\n")
        else:
            # Unknown prompt type
            self.status_label.setText(f"[Input Required] {prompt}")
            self.console.append_line(f"\n>>> {prompt}")
            self.accept_btn.setVisible(True)
            self.retry_btn.setVisible(True)
            self.giveup_btn.setVisible(True)

    def _on_accept_reading(self) -> None:
        if not self._session:
            return
        # Check button text to determine what to send
        if self.accept_btn.text() == "Yes (y)":
            self._session.send_key("y")
        else:
            self._session.send_key("\n")
        self._reset_and_hide_buttons()

    def _on_retry_reading(self) -> None:
        if not self._session:
            return
        if self.retry_btn.text() == "No (n)":
            self._session.send_key("n")
        else:
            self._session.send_key(" ")
        self._reset_and_hide_buttons()

    def _on_giveup_reading(self) -> None:
        if self._session:
            self._session.send_key("q")
        self._reset_and_hide_buttons()

    def _reset_and_hide_buttons(self) -> None:
        """Reset button labels and hide them."""
        self.accept_btn.setText("Accept (Enter)")
        self.retry_btn.setText("Retry")
        self.giveup_btn.setText("Give Up (q)")
        self.accept_btn.setVisible(False)
        self.retry_btn.setVisible(False)
        self.giveup_btn.setVisible(False)

    def _on_measured_colors_updated(self, colors: dict[str, tuple[int, int, int]]) -> None:
        """Update chart with measured colors from TI3."""
        self.chart_view.update_measured_colors(colors)

    def _on_finished(self, code: int) -> None:
        self.console.append_line(f"chartread exited with code {code}")
        self._reset_and_hide_buttons()
        self._ti3_watcher.stop_watching()
        # Emit signal on successful completion with the ti3 path
        if code == 0 and self._current_ti3_path:
            self.measurementsComplete.emit(self._current_ti3_path)
        self._current_strip = None
        self._target_strip = None
        self._initial_strip_seen = False
        self._auto_done_sent = False
        self._session = None
        self.chart_view.highlight_strip(None)

        # Refresh split view from the freshly-written .ti3.
        if self._current_chart_path:
            ti3_path = self._current_chart_path.with_suffix(".ti3")
            if ti3_path.exists():
                self._load_existing_ti3(ti3_path)
            else:
                self.chart_view.set_measurement_mode(False)

        if self._pending_restart and self._current_chart_path:
            # Auto save+restart cycle: relaunch chartread with -r -N so it
            # resumes from the .ti3 and skips re-calibration.
            self._pending_restart = False
            self._on_start(no_calibrate=True)
            return

        # Final stop: clear target and restore start/load buttons.
        self._target_strip = None
        self.start_button.setVisible(True)
        self.load_button.setVisible(True)
        self.stop_button.setVisible(False)

