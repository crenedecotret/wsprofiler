# Audit: TI3 Combiner Wiring in the Optimisation Wizard

## Question
Is the ti3 combiner correctly wired into the optimisation wizard? Can the code reliably find both the **initial ti3** (pass-1 measurements) and the **optimisation ti3** (add-on measurements)?

## Verdict: Yes — the wiring is correct under normal conditions

Both files are found reliably during a live session. The WSP save/load path is also correct. There are a few minor observations below but **no blocking bugs**.

---

## How the two TI3 files are tracked

| File | Data structure | Set by | Role in WSP |
|------|---------------|--------|-------------|
| Initial (pass-1) ti3 — e.g. `session.ti3` | `OptimisePage._original_pass["ti3"]` | `set_original_pass_data()` from `wizard._on_profile_generated()` | `"ti3"` |
| Optimisation ti3 — e.g. `session_opt1.ti3` | `OptimisePage._optimisation_passes[N]["ti3"]` | `_on_measurements_complete()` after chartread finishes | `"pass{i}_ti3"` |
| Combined ti3 — e.g. `session_opt1_combined1.ti3` | `OptimisePage._combined_ti3s` list | `_run_build_profile()` after `ti3_combiner.combine_all()` | `"combined{i}_ti3"` |

## Live session flow (correct)

1. **Profile generated** → `wizard._on_profile_generated()` (wizard.py:433)
   - Derives `ti3_path = target.with_suffix(".ti3")` from the session stem (`"session"`)
   - Calls `opt_page.set_original_pass_data(files={"ti1": ..., "ti2": ..., "ti3": ..., "icc": ...})`
   - `_original_pass["ti3"]` = `/tmp/wsprofiler_xxx/session.ti3` ✅

2. **Add-on chart generated** → `_start_add_on_generation()` (optimise_page.py:770)
   - Reads `pass1_ti3` from `_combined_ti3s[-1]` (if exists) or `_original_pass["ti3"]` ✅
   - Passes to `pass2_generator.generate_pass2_ti1()` ✅

3. **Add-on measured** → `_on_measurements_complete()` (optimise_page.py:912)
   - Sets `_optimisation_passes[-1]["ti3"] = ti3_path` ✅
   - `ti3_path` = `/tmp/wsprofiler_xxx/session_opt1.ti3` (from embedded MeasurementPage) ✅

4. **Build optimised profile** → `_run_build_profile()` (optimise_page.py:925)
   - Collects sources: `[_original_pass["ti3"]] + [p["ti3"] for p in _optimisation_passes]`
   - Both paths exist on disk ✅
   - Calls `ti3_combiner.combine_all(sources, combined)` ✅
   - Writes to `session_opt1_combined1.ti3` ✅

## WSP save/load flow (correct)

**Save** (`wizard._collect_optimisation_files()`, wizard.py:707):
- Original pass keys: `"ti1"`, `"ti2"`, `"ti3"`, `"icc"` — from `opt_page._original_pass` ✅
- Optimisation pass keys: `"pass{i}_ti1"`, `"pass{i}_ti2"`, `"pass{i}_ti3"`, `"pass{i}_icc"` ✅
- Combined keys: `"combined{i}_ti3"` ✅
- All paths checked with `.exists()` before adding to manifest ✅

**Load** (`wizard.load_wsp_session()`, wizard.py:165):
- Original: `files.get("ti3")` → passed to `set_original_pass_data()` ✅
- Optimisation: `files.get(f"pass{i}_ti3")` → stored in `_optimisation_passes` ✅
- Combined: `files.get(f"combined{i}_ti3")` → stored in `_combined_ti3s` ✅
- Also has fallback scan (wizard.py:194-203) for corrupted old WSPs ✅

## Minor observations (non-blocking)

### 1. `set_original_pass_data()` resets optimisation state

`set_original_pass_data()` clears `_optimisation_passes` and `_combined_ti3s` (optimise_page.py:474-475). During WSP load, `wizard.load_wsp_session()` calls this first (line 282), then immediately repopulates the lists (lines 290-301). This is harmless (no data loss) but is a code smell — the reset-then-repopulate could be cleaner.

### 2. No file-existence check before combine

`_run_build_profile()` (lines 938-946) adds paths to the sources list without checking `.exists()`. If a file was deleted between sessions, `combine_all()` would throw. This is caught by the try/except (lines 961-967) with a user-visible error message, so it fails gracefully.

### 3. Combined file naming is slightly redundant

The combined file is named `session_opt1_combined1.ti3` (the `_opt{N}_combined{N}` pattern has both pass number twice). This is cosmetic — functionally correct.

## Test coverage

- `test_ti3_combiner.py` — covers `combine`, `combine_all`, mismatched formats ✅
- `test_optimise_page.py` — covers WSP round-trip, `set_original_pass_data`, `_latest_icc_path`/`_latest_ti3_path`, `load_wsp_session` with optimisations ✅
- No integration test that exercises the full live session flow (generate → measure → profile → optimise → combine → build) — this would require Argyll binaries on PATH

## Conclusion

**No code changes needed.** The ti3 combiner is correctly wired in. Both the initial ti3 and optimisation ti3 are reliably found during both live sessions and WSP load/restore.
