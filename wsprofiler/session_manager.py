"""Simple SessionManager: orchestrates the wizard's working files.

A session owns a temporary working directory and a session archive (.wsp).
The working directory holds all intermediate ArgyllCMS files (ti1, ti2, ti3,
tiff, icc). The .wsp archive is auto-saved at each step so the user can
resume later. When the final ICC is created it is copied to the user's
chosen destination; the temp dir can be discarded at that point.
"""
from __future__ import annotations

import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .session import (
    CURRENT_VERSION,
    SimpleSnapshot,
    WIZARD_TYPE_SIMPLE,
    load_archive,
    save_session,
)
from .ui.session_controller import _auto_increment_path


# How long (seconds) a temp session dir is kept before being cleaned up.
_TEMP_DIR_MAX_AGE = 7 * 24 * 60 * 60  # 7 days


class SessionManager:
    """Owns a single wizard session: temp working dir, WSP path, final ICC."""

    def __init__(self) -> None:
        self._temp_dir: Path | None = None
        self._wsp_path: Path | None = None
        self._final_icc_path: Path | None = None
        self._is_loaded: bool = False
        self._source_wsp: Path | None = None  # where we loaded from (for resave)
        self._target_stem: str = "session"  # stem used for Argyll files & WSP name

    # ------------------------------------------------------------------ temp
    @property
    def temp_dir(self) -> Path | None:
        return self._temp_dir

    @property
    def is_loaded(self) -> bool:
        return self._is_loaded

    @property
    def is_active(self) -> bool:
        return self._temp_dir is not None and self._temp_dir.exists()

    @property
    def target_stem(self) -> str:
        """Stem name used for Argyll binaries inside the temp dir."""
        return self._target_stem

    @target_stem.setter
    def target_stem(self, value: str) -> None:
        self._target_stem = value

    @property
    def target_path(self) -> Path | None:
        """Full target stem path inside the temp dir (e.g. /tmp/wsp/session)."""
        if self._temp_dir is None:
            return None
        return self._temp_dir / self._target_stem

    @property
    def wsp_path(self) -> Path | None:
        return self._wsp_path

    @property
    def default_sessions_dir(self) -> Path:
        """Return the directory where WSP sessions are saved by default.

        Resolves to the platform-appropriate app data location with
        ``/sessions`` appended. Uses ``QStandardPaths.AppDataLocation``
        which yields:
          - Linux: ``$XDG_DATA_HOME/wsprofiler/sessions``
          - macOS: ``~/Library/Application Support/wsprofiler/sessions``
          - Windows: ``%APPDATA%/wsprofiler/sessions``

        Requires that the QApplication has its applicationName set to
        "wsprofiler" before being called. organizationName is left
        empty to avoid the double-nested ``<org>/<app>`` path Qt
        produces when both are set to the same value.
        """
        from PySide6.QtCore import QCoreApplication, QStandardPaths
        if not QCoreApplication.applicationName():
            raise RuntimeError(
                "QApplication.applicationName() must be set before using "
                "SessionManager. The production app sets it in app.main(); "
                "tests should use the qapp fixture in conftest."
            )
        base = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
        if not base:
            # Fallback only if Qt fails to give us a path (shouldn't
            # happen on the platforms we support).
            base = str(Path.home() / ".local" / "share" / "wsprofiler")
        return Path(base) / "sessions"

    @property
    def final_icc_path(self) -> Path | None:
        return self._final_icc_path

    @final_icc_path.setter
    def final_icc_path(self, value: Path | None) -> None:
        self._final_icc_path = value

    # -------------------------------------------------------- session setup
    def set_final_icc_path(self, path: Path) -> None:
        """Record where the user wants the final .icc delivered."""
        self._final_icc_path = path

    def create_new(self) -> Path:
        """Start a brand-new session.

        Clears any previous temp directory and creates a new one. Computes
        a default WSP save path in the platform app-data location.
        """
        self._cleanup_old_sessions()
        self.cleanup()
        self._temp_dir = Path(tempfile.mkdtemp(prefix="wsprofiler_"))
        self._is_loaded = False
        self._source_wsp = None
        self._wsp_path = self._compute_default_wsp_path()
        return self._temp_dir

    def load_from_wsp(self, wsp_path: Path) -> tuple[dict[str, Any], dict[str, Path], Path]:
        """Extract a .wsp archive into a new temp directory.

        Returns (manifest, files, temp_dir). The temp_dir is also stored
        on the instance and is the working directory for all pages.
        """
        self._cleanup_old_sessions()
        self.cleanup()
        self._temp_dir = Path(tempfile.mkdtemp(prefix="wsprofiler_wsp_"))
        manifest, files = load_archive(wsp_path, self._temp_dir)
        self._source_wsp = wsp_path
        self._wsp_path = wsp_path  # auto-save overwrites the loaded file
        self._is_loaded = True
        return manifest, files, self._temp_dir

    # ------------------------------------------------------------- save/load
    def auto_save(
        self,
        file_paths: dict[str, Path],
        generate_config: dict[str, Any] | None = None,
        profile_configs: list[dict[str, Any]] | None = None,
        optimisation_count: int = 0,
        measurement_complete: bool = False,
    ) -> Path | None:
        """Silently save/update the .wsp archive.

        Returns the path written, or None if no temp_dir / wsp_path is set.
        """
        if self._temp_dir is None or self._wsp_path is None:
            return None

        snapshot = SimpleSnapshot(
            version=CURRENT_VERSION,
            wizard_type=WIZARD_TYPE_SIMPLE,
            target_name=self._target_stem,
            workspace=str(self._temp_dir),
            generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            measurement_complete=measurement_complete,
            generate_config=generate_config or {},
            profile_configs=profile_configs or [],
            optimisation_count=optimisation_count,
        )

        self._wsp_path.parent.mkdir(parents=True, exist_ok=True)
        save_session(self._wsp_path, snapshot, self._temp_dir, file_paths)
        return self._wsp_path

    def copy_final_icc(self, source_icc: Path) -> Path | None:
        """Copy the generated ICC to the user's chosen final destination.

        Creates parent directories as needed. Returns the final destination
        path, or None if no destination was set.
        """
        if self._final_icc_path is None or not source_icc.exists():
            return None
        dest = self._final_icc_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_icc, dest)
        return dest

    # --------------------------------------------------------------- cleanup
    def cleanup(self) -> None:
        """Remove the temp working directory, if any."""
        if self._temp_dir is not None and self._temp_dir.exists():
            shutil.rmtree(self._temp_dir, ignore_errors=True)
        self._temp_dir = None
        self._wsp_path = None
        self._final_icc_path = None
        self._is_loaded = False
        self._source_wsp = None

    def _compute_default_wsp_path(self) -> Path:
        """Return the default WSP save path with target name and date."""
        sessions_dir = self.default_sessions_dir
        sessions_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now().strftime("%Y%m%d")
        candidate = sessions_dir / f"{self._target_stem}_{date_str}.wsp"
        if not candidate.exists():
            return candidate
        return _auto_increment_path(candidate)

    def _cleanup_old_sessions(self) -> None:
        """Remove orphaned wsprofiler temp dirs older than _TEMP_DIR_MAX_AGE."""
        temp_root = Path(tempfile.gettempdir())
        now = time.time()
        for entry in temp_root.glob("wsprofiler_*"):
            if not entry.is_dir():
                continue
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                continue
            if now - mtime > _TEMP_DIR_MAX_AGE:
                shutil.rmtree(entry, ignore_errors=True)
