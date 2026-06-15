# wsprofiler Agent Guidance

## Development Setup
- **Prerequisites**: Python 3.10+, Argyll CMS binaries (`targen`, `printtarg`, `chartread`, `colprof`) on PATH
- **Install**: `python -m venv .venv && source .venv/bin/activate && pip install -e .`
- **Windows**: Use `.venv\Scripts\activate`; `pywin32` auto-installed for console interaction with ArgyllCMS tools

## Running the Application
- **Dev mode**: `python -m wsprofiler` or `wsprofiler` (after install)
- **Entry point**: `wsprofiler/__main__.py` → `wsprofiler/app.py:main()`

## Testing
- **Run tests**: `pytest` (test dependencies in `[project.optional-dependencies] test`)
- **Test files**: Located in `wsprofiler/tests/`

## Building Executables
- **Build deps**: `pip install -e ".[build]"` (installs pyinstaller)
- **Build cmd**: `pyinstaller wsprofiler.spec --clean --noconfirm`
- **Linux**: Use `upx` (e.g., `apt-get install upx-ucl`) for binary compression as seen in CI
- **Output**: `dist/wsprofiler` (Linux) or `dist/wsprofiler.exe` (Windows)
- **CI**: See `.github/workflows/build-*.yml` for automated builds

## Project Structure
- **GUI**: `wsprofiler/ui/` (MainWindow, wizard pages, chart visualization)
- **Two-Step Wizard**: `wsprofiler/ui/two_step_dialog.py` (guided two-pass profiling workflow)
- **Core**: `wsprofiler/ti/` (CGATS/.ti2 parsing), `wsprofiler/argyll/` (subprocess wrappers)
- **Helper**: `pkpatches.py` (standalone tool for generating advanced patch sets)
- **Spec file**: `wsprofiler.spec` controls PyInstaller bundling

## Important Notes
- Argyll CMS must be installed separately; GUI won't function without binaries in PATH
- `.ti2` sample data in `assets/sample/sample_chart.ti2` for testing without full chart generation
- Measurement page handles `chartread` process interaction (streams stdout/stderr, sends calibration keystrokes)