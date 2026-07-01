# Plan: Rename Step 1 Label to "Generate Chart" in Two-Pass Wizard

## Goal
Change the step bar label for Step 1 in the two-pass wizard from `"Generate"` to `"Generate Chart"` to better describe what the user does on that step.

## Location
**File:** `wsprofiler/ui/two_step_dialog.py`  
**Line:** 233

## Current Code
```python
step_names = ["Generate", "Measure First Chart", "Generate Second Chart", "Read Refinement Chart", "Done"]
```

## Proposed Change
Change `"Generate"` to `"Generate Chart"`:
```python
step_names = ["Generate Chart", "Measure First Chart", "Generate Second Chart", "Read Refinement Chart", "Done"]
```

## Impact
- Only a single string literal change on one line.
- No other code changes needed (no references to step names by string elsewhere — they are only used for display in the `StepBar`).
- The step bar rendering, click handling, and page mapping remain unaffected since the number and order of steps are unchanged.

## Verification
- Launch the two-pass wizard (e.g., via the main wizard's "Refined 2-Pass Profiling" button) and visually confirm Step 1 shows "Generate Chart".
- Run `pytest` to ensure no existing tests break.
