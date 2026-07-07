# Plan: Fix Session Manager UI quirks

## Goal

Fix two visual/UX defects in `SessionManagerDialog`:

1. **Bold column headers on row selection** — when the user selects a session row, the table's column headers ("Profile Name", "Step", "Saved At", "Filename") render in bold. This is unintentional and inconsistent with the rest of the app's typography.
2. **Layout jumps when the last session is deleted** — once every `.wsp` is removed, the table is hidden and the empty-state label takes over. Because only the table has a vertical stretch factor, the dialog collapses to its minimum height and the button row jumps up to sit just below the empty label, making the dialog visibly shrink and the buttons move.

## Root-cause summary

Both issues live in `wsprofiler/ui/session_manager_dialog.py`. There is **no application code** that sets bold on headers or that manipulates the layout on delete — both behaviours are emergent from how the dialog is constructed.

| # | Behaviour | Root cause | Location |
|---|-----------|------------|----------|
| 1 | Headers bold on selection | `QTableWidget.horizontalHeader().highlightSections` is `True` by default. Qt's native style (and our `dark.qss`, which has no `QHeaderView` rules) renders "highlighted" header sections in **bold**. The selection handler (`_on_selection_changed`) only toggles button enablement and does not touch headers — so no custom code is at fault. | `session_manager_dialog.py:60-75` (table setup) |
| 2 | Dialog collapses when empty | Layout order: title → empty label → table (`stretch=1`) → buttons. The empty label is added with **no stretch** (`session_manager_dialog.py:57`). When `_scan_sessions` hides the table and shows the empty label, no visible widget has a stretch factor, so the QVBoxLayout collapses to the minimum size of the title + empty label + buttons. | `session_manager_dialog.py:52-57` (label add), `113-133` (visibility toggling) |

## Files touched

- `wsprofiler/ui/session_manager_dialog.py` — two single-line edits. No new files, no new dependencies, no API changes.

## Tasks

### Task 1 — Stop headers from bolding on selection

**File:** `wsprofiler/ui/session_manager_dialog.py`
**Where:** in `__init__`, immediately after the `setAlternatingRowColors(True)` call and before the column-width calls (i.e. just below the existing table-setup block at line 69). This keeps header-related configuration grouped together.

**Change:** add one line:

```python
self._table.horizontalHeader().setHighlightSections(False)
```

**Why this works:** `QHeaderView.highlightSections` controls whether the section text is rendered in the "selected" state (which Qt's native style paints as bold). Setting it to `False` makes the header sections render identically whether or not a row is selected. Selection feedback remains visible on the row itself (the row's background highlight is unaffected), so users still get a clear selection cue.

**No other change is required.** We deliberately do not add QSS rules for `QHeaderView` — `setHighlightSections(False)` is the minimum-surface fix and avoids introducing theme rules that could leak into other tables in the app.

**Verification:**
- Launch the app, open the session manager (`Load Session` button in the wizard).
- Select a row. Confirm the column headers stay the same weight as before selection.
- Confirm the selected row itself is still visually highlighted (selection cue preserved).
- Run `pytest wsprofiler/tests/` — existing tests in `test_session_manager_dialog.py` should still pass; `test_delete_removes_file_and_refreshes` exercises both selection and empty-state transitions and must remain green.

---

### Task 2 — Stabilise the layout when the session list is empty

**File:** `wsprofiler/ui/session_manager_dialog.py`
**Where:** line 57, where the empty-state label is added to the vertical layout.

**Change:** replace

```python
layout.addWidget(self._empty_label)
```

with

```python
layout.addWidget(self._empty_label, stretch=1)
```

**Why this works:** Qt layouts distribute extra space only among widgets that have a non-zero stretch factor *and are visible*. Currently the table is the only widget with `stretch=1`. When the table is hidden, all extra vertical space is unclaimed and the dialog shrinks to the natural size of its remaining visible children (title + empty label + buttons).

By giving the empty label a matching `stretch=1`, whichever of the two — table or empty label — is currently visible will claim the extra space, keeping the button row anchored to the bottom of the dialog in both states. No new widgets, no `QStackedWidget` refactor, no spacer — just a one-character change to the existing call.

**Why not hide the table differently or use a stacked widget:** both would be larger refactors for a layout-stability bug. The stretch-on-the-empty-label approach is the smallest correct fix and matches Qt's normal pattern of "the currently-visible expanding widget absorbs the slack."

**Verification:**
- Open the session manager with at least one saved session. Note the dialog height and the position of the button row.
- Delete every session one by one. Confirm:
  - The dialog height does not visibly change after the last delete.
  - The button row stays at the bottom of the dialog (or at least at the same vertical position it was at when the table was populated).
  - The empty label is centred vertically within the previously-table area.
- Re-add a session (or close and reopen the dialog) — confirm the table reappears and the button row is still anchored at the bottom.
- Run `pytest wsprofiler/tests/` — must remain green.

---

## Out of scope (deliberately)

- No refactor of `_scan_sessions` to a model/view architecture. The current `QTableWidget` is sufficient and the existing test suite is built around it.
- No changes to `dark.qss`. The header fix is intentionally a single Python call, not a global theme rule, so it does not risk changing the appearance of any other table in the app.
- No new tests. The fixes are two one-line changes; the existing `test_session_manager_dialog.py` (especially `test_empty_state` and `test_delete_removes_file_and_refreshes`) already covers the affected code paths and will catch any regression.
- No changes to the delete confirmation flow, button enablement, or selection behaviour.

## Risk assessment

Very low. Both changes are one-liners confined to `SessionManagerDialog.__init__` and one of its `addWidget` calls. They do not alter:
- Public API (`selected_path`, `is_new_session`, signal connections).
- The persistence layer in `wsprofiler/session.py`.
- The `SessionManager` state in `wsprofiler/session_manager.py`.
- The wizard's call site in `wsprofiler/ui/wizard.py:142-163`.
- Any other dialog or table in the application.

Existing tests pass unchanged because the visible behaviour they assert (button enablement, row count, label visibility on empty) is preserved.
