# AGENTS.md

PySide6 desktop GUI wrapping the Argyll CMS color-profile pipeline
(targen → printtarg → chartread → colprof). Python 3.10+.

## Commands

- **Install (editable):** `pip install -e .`
- **Run the app:** `python -m wsprofiler` (entry: `wsprofiler/__main__.py` → `wsprofiler/app.py:main`)
- **Tests:** `pip install -e ".[test]"`, then `pytest wsprofiler/tests/`
- **Build standalone exe:** `pip install -e ".[build]"` then `pyinstaller wsprofiler.spec --clean --noconfirm` (output in `dist/`)

There is **no lint, typecheck, or formatter config** in this repo. Do not invent commands for them.

## Run tests the right way

- Test files live in `wsprofiler/tests/`. Run `pytest wsprofiler/tests/`, **not** bare `pytest` from the repo root.
- `test_sample_files.py` at the **repo root** is a manual integration script, not a pytest test. It executes top-level code on import (prints + `subprocess` calls), so bare `pytest` collects and runs it at collection time and fails. Scope it out by pointing pytest at `wsprofiler/tests/`.
- `wsprofiler/tests/conftest.py` sets `QT_QPA_PLATFORM=offscreen` and redirects `XDG_DATA_HOME` / `APPDATA` / `HOME` to a temp dir so Qt app data never touches the real home. Do not remove these.
- Tests that shell out to Argyll binaries (`chartread`, `xicclu`, `printtarg`, …) will fail if those tools are not on `PATH`.

## Package layout quirk

`pyproject.toml` sets `[tool.setuptools] package-dir = {"" = "wsprofiler"}`. The repo-root `wsprofiler/` directory **is** the importable `wsprofiler` package (so `import wsprofiler` → `wsprofiler/__init__.py`). The nested empty `wsprofiler/wsprofiler/{argyll,ti,ui}/` directories are stale leftovers — ignore them, do not add code there.

## Critical Qt convention

`app.setOrganizationName("")` is **intentionally empty** (`wsprofiler/app.py:18`, mirrored in conftest). This makes `QStandardPaths.AppDataLocation` resolve to `<data>/wsprofiler` rather than `<data>/wsprofiler/wsprofiler`. Do not set an organization name anywhere — it will silently relocate session storage and break `SessionManager`.

## Architecture

- `wsprofiler/ui/` — wizard shell + pages, chart view, chartread dialog, log console, theme. `main_window.py` is the entry widget; `wizard.py` orchestrates the pipeline pages under `ui/pages/`.
- `wsprofiler/argyll/` — subprocess wrappers around Argyll tools. `chartread.py` / `runner.py` handle **interactive single-keypress stdin** (chartread needs tcsetattr-style input), with Windows-specific handling via `pywin32`.
- `wsprofiler/ti/` — lightweight CGATS reader and `.ti2`/`.ti3` parsing (`cgats.py` is the base).
- `wsprofiler/profiling/pass2_generator.py` — calls `xicclu` to generate a pass-2 `.ti1` from a pass-1 `.ti3` + `.icc`.
- `wsprofiler/session.py` — saves/loads wizard sessions as `.wsp` (simple) / `.wsp2` (two-step) **zip archives with a `manifest.json`** plus the referenced `.ti1/.ti2/.ti3/.icc` files. `CURRENT_VERSION == 1`; `load_session` raises on version mismatch.
- `wsprofiler/platform.py` — `is_windows()` / `is_linux()` / `is_macos()` helpers; prefer these over `sys.platform` checks.

## External prerequisite

Argyll CMS binaries must be installed and on `PATH`: `targen`, `printtarg`, `chartread`, `colprof`, `xicclu` (see https://www.argyllcms.com). `pywin32` is a Windows-only dependency declared in `pyproject.toml`.