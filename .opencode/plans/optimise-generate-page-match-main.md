# Plan: Make Optimise Generate Page Match Main Generate Page

## Problem

After the add-on chart generation completes on the Optimise page, the UI switches to a `GEN_DONE` state that shows a "Chart generated! Ready to measure." label and a "Measure Chart" button, hiding all the chart-option form widgets and the console checkbox.

The main Generate page behaves differently: after generation completes, the form remains visible (with the Generate button re-shown), the console/output remains toggleable, and the wizard shows a print dialog with file locations. The user wants the Optimise Generate sub-page to act **exactly** like the main Generate page.

## Current Behavior (Optimise Page)

In `_on_printtarg_done()` (line 869-898 of `optimise_page.py`):
1. Stores pass data, emits `passChartsGenerated` signal (wizard shows print dialog)
2. Loads ti2 into measurement page
3. **Hides** `_show_console_check` (line 897)
4. **Switches** `_gen_stack` to `GEN_DONE` (line 898) — which hides the form and shows the "ready to measure" label + button

## Current Behavior (Main Generate Page)

In `_on_all_done()` (line 644-649 of `generate_page.py`):
1. Shows `generate_btn`, hides `cancel_btn`
2. Emits `chartGenerated` signal (wizard shows print dialog, then navigates to Measure page)
3. Console and preview remain visible/hidden per checkbox state (no change)

## Changes

**File: `wsprofiler/ui/pages/optimise_page.py`**

### 1. Modify `_on_printtarg_done()` (lines 896-898)

Replace:
```python
        # Switch to GEN_DONE (within Generate sub-page)
        self._show_console_check.setVisible(False)
        self._gen_stack.setCurrentIndex(self.GEN_DONE)
```

With:
```python
        # Return to the form (like the main Generate page)
        self._gen_stack.setCurrentIndex(self.GEN_READY)
        self._generate_addon_btn.setVisible(True)
```

This:
- Switches back to `GEN_READY` (the form with chart options) instead of `GEN_DONE`
- Re-shows the "Generate Add-on Chart" button (mirroring `generate_btn.setVisible(True)`)
- Keeps `_show_console_check` visible so the user can toggle console output
- The print dialog still shows (it's triggered by `passChartsGenerated.emit()` on line 891, which the wizard catches)

### 2. Hide the "Generate Add-on Chart" button during generation

In `_start_add_on_generation()` (around line 796), add a line to hide the button (mirroring how the main Generate page hides its button during generation):

```python
        self._generate_addon_btn.setVisible(False)
```

This should be added right after the `GEN_GENERATING` stack switch (line 796), so the button disappears while generation is in progress.

### 3. Optionally remove the `GEN_DONE` state (cleanup)

The `GEN_DONE` state (lines 279-296) and its associated widgets (`_gen_done_label`, `_goto_measure_btn`) are no longer needed. They can be removed, along with the `GEN_DONE = 2` constant (line 125). However, since `_gen_stack` only uses indices 0 and 1 after this change, the `GEN_DONE` widget can simply be left as dead code or removed for cleanliness.

**Recommended:** Remove the `GEN_DONE` widget and constant to avoid confusion.

## Files to Modify

| File | Change |
|------|--------|
| `wsprofiler/ui/pages/optimise_page.py` | Modify `_on_printtarg_done()`, `_start_add_on_generation()`, remove `GEN_DONE` state |

## Verification

1. Run the app: `python -m wsprofiler`
2. Complete a pass-1 profile (generate → measure → profile)
3. Navigate to the Optimise page and generate an add-on chart
4. After generation completes, verify:
   - The print dialog appears with file locations (same as main Generate page)
   - After dismissing the dialog, the form is visible with "Generate Add-on Chart" button
   - The console checkbox is visible and toggles the output
   - There is NO "Chart generated! Ready to measure." label or "Measure Chart" button
5. Run tests: `pytest wsprofiler/tests/`
