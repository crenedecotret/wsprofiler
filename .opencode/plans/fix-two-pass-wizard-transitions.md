# Plan: Fix Two-Pass Wizard Step 2→3 Transition & UX Issues

## Summary
The two-pass wizard's transitions between steps 2→3 and 3→4 have several recoverability gaps and UX inconsistencies. This plan addresses them in priority order.

---

## 1. Title Label Alignment (Quick / Low Risk)

**File:** `wsprofiler/ui/two_step_dialog.py`, `_go_to_page()` method, lines 599–620

The page titles shown in the header still use old names and don't match the step bar labels the user just changed. Fix:

| Page | Current Title | New Title |
|---|---|---|
| `PAGE_GENERATE` | `"Generate First Chart"` | `"Generate Chart"` |
| `PAGE_MEASURE1` | `"Measure First Chart"` | `"Measure Chart"` |
| `PAGE_AUTO1` | `"Generate Second Chart"` | `"Refinement Chart"` |
| `PAGE_MEASURE2` | `"Read Refinement Chart"` | `"Measure Chart"` |

**Impact**: Pure string changes, no logic affected.

---

## 2. Add Cancel/Retry to Auto1 Page (Medium / Visual)

**File:** `wsprofiler/ui/two_step_dialog.py`

### 2a. Add Cancel button to `_setup_auto1_page()`
- Add a `QPushButton("Cancel")` below the status label, hidden by default.
- Style: secondary/destructive muted style (like `generate_page.py` cancel button).
- Connect to `_on_auto1_cancel()`.

### 2b. Add Retry button to `_setup_auto1_page()`
- Add a `QPushButton("Retry")` below the Cancel button, hidden by default, with accent-button style.
- Connect to `_run_auto1()`.

### 2c. Wire button visibility
- **Processing starts** (top of `_run_auto1`): hide Retry, show Cancel. Set status to neutral.
- **Failure occurs** (in `_on_precond_done`, `_on_pass2_failed`, `_on_chart2_printtarg_done`): hide Cancel, show Retry. Make status red via stylesheet.
- **Success** (in `_on_chart2_printtarg_done`): hide both buttons, auto-advance to PAGE_MEASURE2.

### 2d. `_on_auto1_cancel()`
```python
def _on_auto1_cancel(self):
    # Kill QProcess if running (colprof or printtarg)
    if self._proc:
        self._proc.kill()
        self._proc = None
    # Quit thread if running (Pass2Worker)
    if self._pass2_thread and self._pass2_thread.isRunning():
        self._pass2_thread.quit()
        self._pass2_thread.wait(3000)  # max 3s wait
        self._pass2_thread = None
        self._pass2_worker = None
    self._auto1_status.setStyleSheet("font-size: 18px; color: #e63946; font-weight: 500;")
    self._auto1_status.setText("Cancelled.")
    self._auto1_cancel_btn.setVisible(False)
    self._auto1_retry_btn.setVisible(True)
```

---

## 3. ICC File Existence Check After colprof (Medium / Defensive)

**File:** `wsprofiler/ui/two_step_dialog.py`, `_on_precond_done()`, line ~874

After colprof exits with code 0 and the ICC is renamed, verify the precond ICC actually exists:

```python
if not precond_path.exists():
    self._auto1_status.setStyleSheet("font-size: 18px; color: #e63946; font-weight: 500;")
    self._auto1_status.setText(
        "Intermediate profile file missing after colprof — "
        "this may indicate an ArgyllCMS error. Click Retry."
    )
    self._auto1_cancel_btn.setVisible(False)
    self._auto1_retry_btn.setVisible(True)
    self._proc = None
    return
```

This catches the rare case where colprof returns 0 but produces no output.

---

## 4. `_Pass2Worker` Timeout (High / Critical)

**File:** `wsprofiler/ui/two_step_dialog.py`, `_run_auto1()`, around line 917

### 4a. Add a QTimer
- Create a single-shot `QTimer` set to **120 seconds** (2 minutes).
- Start it right before `self._pass2_thread.start()`.
- Store as `self._pass2_timeout_timer`.

### 4b. Handle timeout
```python
def _on_pass2_timeout(self):
    self._pass2_thread.quit()
    self._pass2_thread.wait(3000)
    self._pass2_thread = None
    self._pass2_worker = None
    self._auto1_status.setStyleSheet("font-size: 18px; color: #e63946; font-weight: 500;")
    self._auto1_status.setText(
        "Pass-2 patch generation timed out (2 min). "
        "The ICC profile or xicclu may have hung. Click Retry."
    )
    self._auto1_cancel_btn.setVisible(False)
    self._auto1_retry_btn.setVisible(True)
```

### 4c. Stop timer on completion/failure
- In `_on_pass2_done` and `_on_pass2_failed`: call `self._pass2_timeout_timer.stop()`.
- In `_on_auto1_cancel`: also stop the timer.
- In new closeEvent (see #5): stop the timer.

---

## 5. Dialog Close/Reject Cleanup (Medium / Safety)

**File:** `wsprofiler/ui/two_step_dialog.py`, add to `TwoStepWizardDialog`

Add a `closeEvent` override to clean up any running subprocesses or threads when the user closes the dialog mid-operation:

```python
def closeEvent(self, event):
    """Clean up running processes/threads before closing."""
    # Kill QProcess if running
    if self._proc and self._proc.state() != QProcess.NotRunning:
        self._proc.kill()
        self._proc = None

    # Quit Pass2Worker thread if running
    if self._pass2_thread and self._pass2_thread.isRunning():
        self._pass2_thread.quit()
        self._pass2_thread.wait(3000)
        self._pass2_thread = None
        self._pass2_worker = None

    # Stop timeout timer
    if hasattr(self, '_pass2_timeout_timer') and self._pass2_timeout_timer:
        self._pass2_timeout_timer.stop()

    super().closeEvent(event)
```

Also need to import `QProcess` at the top level (currently it's imported locally in several methods — move a single import to the top).

---

## 6. Bonus: Consistency with Main Wizard's Post-Generation Message

**Already done** — the user already requested this and it was applied. The two-pass wizard's QMessageBox after chart 1 generation now matches the main wizard's ("Charts Ready for Printing" with drying instructions, etc.).

---

## Files Changed
Only one file: **`wsprofiler/ui/two_step_dialog.py`**

---

## Implementation Order
1. **Title labels** (#1) — trivial, no logic risk
2. **Add Cancel/Retry buttons** (#2a–2c) — sets up the UI for recovery
3. **Add Cancel handler** (#2d) — wires the buttons
4. **Add ICC existence check** (#3) — small defensive fix
5. **Add timeout** (#4) — guards against the worst failure mode
6. **Add closeEvent** (#5) — prevents resource leaks

---

## Verification
- Launch two-pass wizard, run through Step 1 (Generate Chart)
- Step 2: Measure (can use sample TI3 from assets)
- Step 3: Auto1 runs. Verify:
  - Cancel button is visible during processing
  - Clicking Cancel stops everything and shows Retry
  - Retry button re-runs auto1 from scratch
  - If colprof were to fail, Retry is shown
  - Title shows "Refinement Chart" (not "Generate Second Chart")
- Step 4: Title shows "Measure Chart" (not "Read Refinement Chart")
- Close dialog mid-auto1: verify no crash, no zombie processes
- Run `pytest` for regression
