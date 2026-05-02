from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from PySide6.QtCore import QObject, Signal

from .runner import create_process_runner, ProcessRunner


# Matches "Ready to read strip pass A" or "Reading row 5" etc.
READ_ROW_RE = re.compile(r"(?:Reading\s+(?:row|strip)|Ready to read strip pass)\s+(?P<row>\w+).*(?:page\s+(?P<page>\d+))?", re.I)
# Calibration prompts: "Place instrument on calibration reference" / "on the white" / "press any key"
PROMPT_CALIBRATE_RE = re.compile(r"(?:place.*calibrat|on\s+(?:the\s+)?white|calibrat.*(?:position|reference|tile))", re.I)
READY_RE = re.compile(r"^Ready$", re.I)
# Error/misread prompts:
# "Hit Return to use it anyway, any other key to retry, Esc or 'q' to give up:"
#   (ERROR_ACCEPT variant - user can accept a questionable read)
# "Hit Esc or 'q' to give up, any other key to retry:"
#   (ERROR_RETRY variant - chartread won't let you keep this reading)
ERROR_PROMPT_RE = re.compile(r"Hit Return|use it anyway|to retry|give up", re.I)
# Confirmation prompts:
ABORT_CONFIRM_RE = re.compile(r"Are you sure.*\[y/n\]", re.I)


@dataclass(slots=True)
class MeasurementStatus:
    page: int | None = None
    strip: int | None = None
    message: str | None = None


class ChartReadSession(QObject):
    statusChanged = Signal(MeasurementStatus)
    stdoutLine = Signal(str)
    stderrLine = Signal(str)
    # Emitted when waiting for user input on error ("Hit Return to use it anyway...")
    waitingForInput = Signal(str)

    def __init__(
        self,
        chart_path: Path,
        executable: Path,
        resume: bool = False,
        no_calibrate: bool = False,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.chart_path = chart_path
        self.executable = executable
        self.resume = resume
        self.no_calibrate = no_calibrate
        self._cancelling = False  # suppress prompt signals while exiting
        self.runner = create_process_runner(self)
        self.runner.stdoutLine.connect(self._handle_stdout)
        self.runner.stderrLine.connect(self.stderrLine)

    def start(self) -> None:
        args = []
        if self.resume:
            args.append("-r")  # Resume existing readings
        if self.no_calibrate:
            args.append("-N")  # Skip initial instrument calibration
        args.append(self.chart_path.stem)
        # ProcessRunner allocates a PTY so chartread's tcsetattr/poll_con_char
        # works correctly for single-keypress reads.
        self.runner.start(str(self.executable), args, cwd=self.chart_path.parent)

    def send_key(self, key: str) -> None:
        self.runner.write(key)

    def cancel(self) -> None:
        """Gracefully stop chartread, saving any measurements read so far.

        Per ArgyllCMS docs, 'd' means "done": chartread writes the patches that
        have been read to the .ti3 file and exits. The session can later be
        resumed with ``chartread -r``. If not all strips are read, chartread
        may prompt to confirm saving; we answer 'y' to any such prompts.
        We schedule the follow-up keypresses asynchronously so Qt's event loop
        keeps draining the PTY while chartread prints its prompts.
        """
        from PySide6.QtCore import QTimer
        self._cancelling = True
        try:
            self.runner.write("d")
        except Exception:
            pass
        # Answer possible follow-up confirmation prompts:
        #   "All strips haven't been read - are you sure? [y/n]:"
        #   "Save the strips that have been read? (y/n):"
        QTimer.singleShot(200, lambda: self._safe_write("y"))
        QTimer.singleShot(450, lambda: self._safe_write("y"))
        QTimer.singleShot(5000, self._force_terminate_if_running)

    def _safe_write(self, key: str) -> None:
        try:
            if self.runner.process.state() != self.runner.process.ProcessState.NotRunning:
                self.runner.write(key)
        except Exception:
            pass

    def _force_terminate_if_running(self) -> None:
        try:
            if self.runner.process.state() != self.runner.process.ProcessState.NotRunning:
                self.runner.terminate()
        except Exception:
            pass

    def _handle_stdout(self, line: str) -> None:
        self.stdoutLine.emit(line)
        if match := READ_ROW_RE.search(line):
            row_str = match.group("row")
            strip = self._strip_label_to_number(row_str)
            page = int(match.group("page") or 1)
            self.statusChanged.emit(MeasurementStatus(page=page, strip=strip, message=line))
        elif ERROR_PROMPT_RE.search(line):
            if self._cancelling:
                return
            # Distinguish between variants
            if "use it anyway" in line.lower():
                self.waitingForInput.emit(f"ERROR_ACCEPT:{line}")
            else:
                self.waitingForInput.emit(f"ERROR_RETRY:{line}")
        elif ABORT_CONFIRM_RE.search(line):
            if self._cancelling:
                # We're in the middle of cancel() which sends 'd' + 'y' itself;
                # don't pop a confirm dialog for the same prompt.
                return
            self.waitingForInput.emit(f"CONFIRM:{line}")
        elif PROMPT_CALIBRATE_RE.search(line):
            if self._cancelling:
                return
            # Calibration requested - emit both status and input prompt
            self.statusChanged.emit(MeasurementStatus(message="Calibrate instrument"))
            self.waitingForInput.emit(f"CALIBRATE:{line}")
        elif READY_RE.search(line):
            self.statusChanged.emit(MeasurementStatus(message="Ready"))
        else:
            self.statusChanged.emit(MeasurementStatus(message=line))

    @staticmethod
    def _strip_label_to_number(label: str) -> int:
        """Convert strip label (A, B, AA, AB) to number (1, 2, 27, 28)."""
        if label.isdigit():
            return int(label)
        num = 0
        for ch in label.upper():
            if ch.isalpha():
                num = num * 26 + (ord(ch) - ord('A') + 1)
        return max(1, num)
