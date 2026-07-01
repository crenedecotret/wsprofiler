"""Combine two ArgyllCMS .ti3 measurement files into one.

Preserves all keywords, data format, and header from the first file, then
appends the data rows from both files with updated NUMBER_OF_SETS.
"""
from __future__ import annotations

from pathlib import Path

from .cgats import load, CGATS


def combine(ti3_a: Path, ti3_b: Path, output: Path) -> None:
    """Merge two .ti3 measurement files into *output*.

    The merged file uses the keywords and data format of *ti3_a* but
    contains patch data from both files.
    """
    combine_all([ti3_a, ti3_b], output)


def combine_all(sources: list[Path], output: Path) -> None:
    """Merge multiple .ti3 measurement files into *output*.

    The merged file uses the keywords and data format of the first source
    but contains patch data from all files.
    """
    if len(sources) < 1:
        raise ValueError("At least one source file is required.")

    cgats_list = [load(src) for src in sources]
    base = cgats_list[0]

    # Validate all use the same data format
    for cgats in cgats_list[1:]:
        if cgats.data_format != base.data_format:
            raise ValueError(
                f"Data format mismatch: {sources[0].name} and source have "
                f"different field layouts."
            )

    combined_data: list[dict[str, str]] = []
    for cgats in cgats_list:
        combined_data.extend(cgats.data)

    # Build output keywords – copy everything from first source, then override counts
    keywords = dict(base.keywords)
    keywords["NUMBER_OF_SETS"] = str(len(combined_data))

    _write_cgats(output, keywords, base.data_format, combined_data)


def _write_cgats(
    path: Path,
    keywords: dict[str, str],
    data_format: list[str],
    data: list[dict[str, str]],
) -> None:
    """Write a CGATS-format file."""
    lines: list[str] = []

    for key, value in keywords.items():
        if value:
            lines.append(f"{key} {value}")
        else:
            lines.append(key)

    lines.append("")
    lines.append("BEGIN_DATA_FORMAT")
    lines.append("\t".join(data_format))
    lines.append("END_DATA_FORMAT")
    lines.append("")
    lines.append("BEGIN_DATA")

    for row in data:
        lines.append("\t".join(row.get(f, "") for f in data_format))

    lines.append("END_DATA")
    lines.append("")

    path.write_text("\n".join(lines))
