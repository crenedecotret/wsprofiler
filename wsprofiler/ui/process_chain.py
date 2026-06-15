"""Sequential QProcess chain with error handling."""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QPlainTextEdit


class ProcessStep:
    """A single step in a process chain."""

    def __init__(
        self,
        exe: Path,
        args: list[str],
        work_dir: str,
        on_finished: Callable[[int], None],
        console: Optional[QPlainTextEdit] = None,
    ) -> None:
        self.exe = exe
        self.args = args
        self.work_dir = work_dir
        self.on_finished = on_finished
        self.console = console


class ProcessChain:
    """Manages a sequence of QProcess steps."""

    def __init__(self, parent: Optional[object] = None) -> None:
        self._parent = parent
        self._steps: list[ProcessStep] = []
        self._current_index = 0
        self._proc: Optional[QProcess] = None
        self._on_error: Optional[Callable[[str], None]] = None
        self._on_all_done: Optional[Callable[[], None]] = None
        self._is_running = False

    def set_error_handler(self, handler: Callable[[str], None]) -> None:
        """Set callback for when any step fails."""
        self._on_error = handler

    def set_completion_handler(self, handler: Callable[[], None]) -> None:
        """Set callback for when all steps complete successfully."""
        self._on_all_done = handler

    def add_step(self, step: ProcessStep) -> None:
        """Add a step to the chain."""
        self._steps.append(step)

    def start(self) -> bool:
        """Start executing the chain. Returns False if already running."""
        if self._is_running:
            return False
        self._is_running = True
        self._current_index = 0
        self._run_current()
        return True

    def _run_current(self) -> None:
        """Run the current step."""
        if self._current_index >= len(self._steps):
            # All done
            self._is_running = False
            if self._on_all_done:
                self._on_all_done()
            return

        step = self._steps[self._current_index]
        self._proc = QProcess(self._parent)
        self._proc.setWorkingDirectory(step.work_dir)
        self._proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)

        # Connect signals
        self._proc.finished.connect(self._on_step_finished)
        if step.console:
            self._proc.readyReadStandardOutput.connect(
                lambda proc=self._proc, console=step.console: self._capture_stdout(proc, console)
            )
        self._proc.errorOccurred.connect(self._on_step_error)

        # Start
        self._proc.start(str(step.exe), step.args)

    def _on_step_finished(self, code: int) -> None:
        """Handle step completion."""
        if code != 0:
            self._cleanup()
            self._is_running = False
            if self._on_error:
                self._on_error(
                    f"Step {self._current_index + 1} failed with exit code {code}"
                )
            return

        # Move to next step
        self._current_index += 1
        self._cleanup_proc()
        self._run_current()

    def _on_step_error(self, error: QProcess.ProcessError) -> None:
        """Handle QProcess error."""
        self._cleanup()
        self._is_running = False
        error_names = {
            QProcess.ProcessError.FailedToStart: "Failed to start",
            QProcess.ProcessError.Crashed: "Crashed",
            QProcess.ProcessError.Timedout: "Timed out",
            QProcess.ProcessError.WriteError: "Write error",
            QProcess.ProcessError.ReadError: "Read error",
        }
        name = error_names.get(error, f"Unknown ({error})")
        if self._on_error:
            self._on_error(f"Process error: {name}")

    @staticmethod
    def _capture_stdout(proc: QProcess, console: QPlainTextEdit) -> None:
        """Capture stdout to console."""
        data = bytes(proc.readAllStandardOutput()).decode("utf-8", "ignore")
        for line in data.splitlines():
            console.appendPlainText(line)

    def _cleanup_proc(self) -> None:
        """Clean up current process without clearing chain state."""
        if self._proc:
            # Disconnect all signals from the old process
            try:
                self._proc.finished.disconnect(self._on_step_finished)
            except RuntimeError:
                pass
            try:
                self._proc.errorOccurred.disconnect(self._on_step_error)
            except RuntimeError:
                pass
            self._proc = None

    def _cleanup(self) -> None:
        """Full cleanup of process and signals."""
        self._cleanup_proc()

    def cancel(self) -> None:
        """Cancel the chain."""
        if self._proc:
            self._proc.kill()
        self._cleanup()
        self._is_running = False

    def is_running(self) -> bool:
        """Check if chain is currently running."""
        return self._is_running
