"""Save/load wizard sessions as .wsp2 (two-step) or .wsp (simple) zip files.

A session packages the wizard's state plus all generated .ti1/.ti2/.ti3/.icc
files into a single portable zip with a JSON manifest.
"""
from __future__ import annotations

import json
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


CURRENT_VERSION = 1
WIZARD_TYPE_TWO_STEP = "two_step"
WIZARD_TYPE_SIMPLE = "simple"
WIZARD_TYPE_OPTIMISED = "optimised"


@dataclass
class GenerateState:
    device: str | None
    paper: str | None
    target_path: str
    show_output: bool = False
    double_density: bool = False
    no_border: bool = False


@dataclass
class MeasureState:
    chart_path: str | None
    current_strip: int | None
    measured_strips: list[int] = field(default_factory=list)
    is_complete: bool = False


@dataclass
class AutoState:
    show_output: bool = False


@dataclass
class DoneState:
    final_icc_path: str | None = None


@dataclass
class WizardSnapshot:
    version: int
    current_page: int
    target_name: str
    workspace: str
    generate: GenerateState
    measure1: MeasureState
    measure2: MeasureState
    auto1: AutoState
    auto2: AutoState
    done: DoneState
    files: dict[str, str] = field(default_factory=dict)
    """role -> relative path inside the zip (e.g. {"chart1_ti2": "chart1.ti2"})"""
    wizard_type: str = WIZARD_TYPE_TWO_STEP


@dataclass
class SimpleSnapshot:
    """Lightweight snapshot for the 3-page main wizard (.wsp archives)."""

    version: int
    wizard_type: str
    target_name: str
    workspace: str
    generated_at: str = ""
    files: dict[str, str] = field(default_factory=dict)
    """role -> relative path inside the zip (e.g. {"ti2": "mytarget.ti2"})"""
    measurement_complete: bool = False
    """Whether all chart rows were read (False = partial read, stay on Measure)."""
    generate_config: dict[str, Any] = field(default_factory=dict)
    """Original generate-page settings (device, paper, pages, etc.)"""
    profile_configs: list[dict[str, Any]] = field(default_factory=list)
    """Profile settings used for each pass (original + optimisations)."""
    optimisation_count: int = 0
    """Number of completed optimisation passes."""


def _snapshot_to_dict(snapshot: Any) -> dict[str, Any]:
    """Convert snapshot to a plain dict for JSON serialization.

    Accepts any dataclass-like object via ``dataclasses.asdict``.
    """
    return asdict(snapshot)


def _dict_to_snapshot(data: dict[str, Any]) -> WizardSnapshot:
    """Reconstruct a WizardSnapshot from a plain dict."""
    return WizardSnapshot(
        version=data["version"],
        current_page=data["current_page"],
        target_name=data["target_name"],
        workspace=data["workspace"],
        generate=GenerateState(**data["generate"]),
        measure1=MeasureState(**data["measure1"]),
        measure2=MeasureState(**data["measure2"]),
        auto1=AutoState(**data["auto1"]),
        auto2=AutoState(**data["auto2"]),
        done=DoneState(**data["done"]),
        files=dict(data.get("files", {})),
        wizard_type=data.get("wizard_type", WIZARD_TYPE_TWO_STEP),
    )


def _dict_to_simple_snapshot(data: dict[str, Any]) -> SimpleSnapshot:
    """Reconstruct a SimpleSnapshot from a plain dict.

    Handles both the original v1 format (no extra fields) and the extended
    format that includes ``generate_config``, ``profile_configs``, and
    ``optimisation_count``.
    """
    return SimpleSnapshot(
        version=data.get("version", CURRENT_VERSION),
        wizard_type=data.get("wizard_type", WIZARD_TYPE_SIMPLE),
        target_name=data["target_name"],
        workspace=data["workspace"],
        generated_at=data.get("generated_at", ""),
        files=dict(data.get("files", {})),
        generate_config=dict(data.get("generate_config", {})),
        profile_configs=list(data.get("profile_configs", [])),
        optimisation_count=data.get("optimisation_count", 0),
    )


def read_manifest(path: Path) -> dict[str, Any]:
    """Read the JSON manifest from a .wsp/.wsp2 archive without extracting.

    Parameters
    ----------
    path
        Path to the zip archive.

    Returns
    -------
    The raw manifest dict, or an empty dict if the archive is unreadable
    or does not contain a ``manifest.json`` entry.
    """
    try:
        with zipfile.ZipFile(path, "r") as zf:
            manifest_bytes = zf.read("manifest.json")
            return json.loads(manifest_bytes)
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError):
        return {}


def save_session(
    path: Path,
    snapshot: Any,
    base_dir: Path,
    file_paths: dict[str, Path],
) -> None:
    """Save a snapshot and its referenced files into a .wsp/.wsp2 zip.

    Parameters
    ----------
    path
        Destination path (e.g. ``something.wsp`` or ``something.wsp2``).
    snapshot
        The wizard state to serialize. Any dataclass works (``WizardSnapshot``
        for two-step wizard, ``SimpleSnapshot`` for the main wizard).
    base_dir
        Directory used to relativize file paths inside the zip.
    file_paths
        Mapping of role -> absolute Path for every file that should be
        included in the archive.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    # Build relative path mapping for the manifest
    rel_files: dict[str, str] = {}
    zip_entries: dict[str, Path] = {}  # arcname -> absolute source path

    for role, abs_path in file_paths.items():
        if not abs_path.exists():
            continue
        try:
            rel = abs_path.relative_to(base_dir)
        except ValueError:
            # File lives outside base_dir; store under a flat name
            rel = Path(abs_path.name)
        arcname = str(rel).replace("\\", "/")
        rel_files[role] = arcname
        zip_entries[arcname] = abs_path

    # Do not mutate the caller's snapshot; build a local dict for the manifest
    manifest_dict = _snapshot_to_dict(snapshot)
    manifest_dict["files"] = rel_files

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        manifest = json.dumps(manifest_dict, indent=2)
        zf.writestr("manifest.json", manifest)
        for arcname, src in zip_entries.items():
            zf.write(src, arcname)


def load_session(path: Path, output_dir: Path) -> WizardSnapshot:
    """Extract a .wsp2 zip and return the snapshot with resolved paths.

    Parameters
    ----------
    path
        Path to the .wsp2 zip file.
    output_dir
        Directory where zip contents will be extracted.

    Returns
    -------
    WizardSnapshot with ``files`` values converted to absolute Paths.

    Raises
    ------
    ValueError
        If the archive is not a two-step wizard session, or if the version
        does not match ``CURRENT_VERSION``.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(path, "r") as zf:
        manifest_bytes = zf.read("manifest.json")
        data = json.loads(manifest_bytes)
        version = data.get("version", 0)
        if version != CURRENT_VERSION:
            raise ValueError(
                f"Unsupported session version {version} (expected {CURRENT_VERSION})"
            )
        wizard_type = data.get("wizard_type", WIZARD_TYPE_TWO_STEP)
        if wizard_type != WIZARD_TYPE_TWO_STEP:
            raise ValueError(
                f"Cannot load a '{wizard_type}' archive as a two-step wizard session"
            )
        zf.extractall(output_dir)

    snapshot = _dict_to_snapshot(data)

    # Resolve relative paths to absolute
    snapshot.files = {
        role: str(output_dir / arcname)
        for role, arcname in snapshot.files.items()
    }

    return snapshot


def load_archive(
    path: Path, output_dir: Path
) -> tuple[dict[str, Any], dict[str, Path]]:
    """Extract a .wsp/.wsp2 zip and return the raw manifest plus resolved file paths.

    This is a generic extractor that does not construct any typed snapshot
    object. Callers (e.g. ``SimpleSessionController.load``) interpret the
    manifest dict as needed.

    Parameters
    ----------
    path
        Path to the zip archive.
    output_dir
        Directory where zip contents will be extracted.

    Returns
    -------
    A tuple ``(manifest, files)`` where ``manifest`` is the raw JSON manifest
    dict and ``files`` is a mapping of role -> absolute Path for every file
    listed in the manifest that exists after extraction.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(path, "r") as zf:
        manifest_bytes = zf.read("manifest.json")
        data = json.loads(manifest_bytes)
        zf.extractall(output_dir)

    files: dict[str, Path] = {}
    for role, arcname in data.get("files", {}).items():
        resolved = output_dir / arcname
        if resolved.exists():
            files[role] = resolved

    return data, files
