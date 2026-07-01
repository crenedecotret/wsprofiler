"""Orchestrates saving and resuming wizard sessions."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..session import (
    CURRENT_VERSION,
    SimpleSnapshot,
    WIZARD_TYPE_SIMPLE,
    load_archive,
    save_session,
)


class SimpleSessionController:
    """Handles save/load for the main wizard (.wsp archives).

    The .wsp archive stores a SimpleSnapshot manifest plus a flat bundle
    of working files. It is the persistent record of a profiling session.
    """

    @staticmethod
    def save(
        workspace: Path,
        target_name: str,
        file_paths: dict[str, Path],
        target_wsp: Path | None = None,
        generate_config: dict[str, Any] | None = None,
        profile_configs: list[dict[str, Any]] | None = None,
        optimisation_count: int = 0,
    ) -> Path:
        """Save a simple wizard snapshot to a ``.wsp`` file.

        Parameters
        ----------
        workspace
            The base directory used to relativize file paths inside the zip.
        target_name
            The target stem (e.g. ``"session"``).
        file_paths
            Mapping of role -> absolute Path. Standard roles:
            ``"ti1"``, ``"ti2"``, ``"ti3"``, ``"tiff"`` (or ``"tiff_1"``…),
            ``"icc"``. Missing files are silently skipped.
        target_wsp
            Output path. If ``None``, defaults to ``workspace / f"{target_name}.wsp"``
            and silently overwrites any existing file at that location.
        generate_config
            Optional generate-page settings to store in the manifest.
        profile_configs
            Optional list of profile settings (one per pass) to store.
        optimisation_count
            Number of completed optimisation passes.

        Returns
        -------
        The path written to.
        """
        if target_wsp is None:
            target_wsp = workspace / f"{target_name}.wsp"

        snapshot = SimpleSnapshot(
            version=CURRENT_VERSION,
            wizard_type=WIZARD_TYPE_SIMPLE,
            target_name=target_name,
            workspace=str(workspace),
            generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            generate_config=generate_config or {},
            profile_configs=profile_configs or [],
            optimisation_count=optimisation_count,
        )

        save_session(target_wsp, snapshot, workspace, file_paths)
        return target_wsp

    @staticmethod
    def load(
        wsp_path: Path,
        output_dir: Path | None = None,
    ) -> tuple[dict[str, Any], dict[str, Path]]:
        """Load a ``.wsp`` archive and return the manifest and resolved files.

        Parameters
        ----------
        wsp_path
            Path to the ``.wsp`` archive.
        output_dir
            Where to extract. Defaults to ``wsp_path.parent``.

        Returns
        -------
        A tuple ``(manifest, files)`` where ``manifest`` is the raw manifest
        dict and ``files`` is the role -> absolute Path mapping.

        Raises
        ------
        ValueError
            If the archive is not a ``simple`` wizard session.
        """
        if output_dir is None:
            output_dir = wsp_path.parent

        manifest, files = load_archive(wsp_path, output_dir)

        wizard_type = manifest.get("wizard_type", WIZARD_TYPE_SIMPLE)
        if wizard_type != WIZARD_TYPE_SIMPLE:
            raise ValueError(
                f"Cannot load a '{wizard_type}' archive as a simple wizard session"
            )

        return manifest, files


def find_tiff_pages(target_stem: Path) -> list[Path]:
    """Find all TIFF chart pages associated with a given target stem.

    ``printtarg`` writes ``<stem>.tif`` for a single page or
    ``<stem>_NN.tif`` for multiple pages (e.g. ``session_01.tif``,
    ``session_02.tif``). This helper locates all such files in the
    target's parent directory and returns them sorted by name.

    Parameters
    ----------
    target_stem
        Path without an extension (e.g. ``/workspace/mytarget``).

    Returns
    -------
        A list of TIFF paths, possibly empty.
    """
    parent = target_stem.parent
    name = target_stem.name
    tiffs = set()
    tiffs.update(parent.glob(f"{name}.tif"))
    tiffs.update(parent.glob(f"{name}_*.tif"))
    return sorted(tiffs)


def _auto_increment_path(path: Path) -> Path:
    """Generate a non-conflicting path like target_1.wsp."""
    stem = path.stem
    parent = path.parent
    suffix = path.suffix
    counter = 1
    while True:
        candidate = parent / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1
