from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from . import cgats


@dataclass(slots=True)
class Patch:
    sample_id: str
    sample_loc: str  # e.g., "G20"
    page: int
    strip: int
    position: int
    device_values: dict[str, float]

    def approx_rgb(self) -> tuple[int, int, int]:
        if {"RGB_R", "RGB_G", "RGB_B"}.issubset(self.device_values):
            r = self._to_8bit(self.device_values["RGB_R"])
            g = self._to_8bit(self.device_values["RGB_G"])
            b = self._to_8bit(self.device_values["RGB_B"])
            return r, g, b

        if {"CMYK_C", "CMYK_M", "CMYK_Y", "CMYK_K"}.issubset(self.device_values):
            # Normalize 0-100 (printtarg default) or 0-1 scale to 0-1
            def norm(v: float) -> float:
                return v / 100.0 if v > 1.0 else v
            c = norm(self.device_values["CMYK_C"])
            m = norm(self.device_values["CMYK_M"])
            y = norm(self.device_values["CMYK_Y"])
            k = norm(self.device_values["CMYK_K"])
            r = (1 - min(1.0, c + k))
            g = (1 - min(1.0, m + k))
            b = (1 - min(1.0, y + k))
            return int(r * 255), int(g * 255), int(b * 255)

        # fallback
        first = next(iter(self.device_values.values()), 0.5)
        tone = self._to_8bit(first)
        return tone, tone, tone

    @staticmethod
    def _to_8bit(val: float) -> int:
        """Convert RGB value to 0-255 range, handling both 0-1 and 0-255 inputs."""
        if val <= 1.0:
            return min(255, max(0, int(val * 255)))
        if val <= 100.0:
            # printtarg typically uses 0-100 scale for device values
            return min(255, max(0, int((val / 100.0) * 255)))
        return min(255, max(0, int(val)))


def load_patches(path: str | Path) -> list[Patch]:
    table = cgats.load(path)
    df = [name.upper() for name in table.data_format]

    def read_float(row: dict[str, str], key: str) -> float:
        try:
            return float(row.get(key, "0"))
        except ValueError:
            return 0.0

    patches: list[Patch] = []
    for row in table.data:
        up_row = {k.upper(): v for k, v in row.items()}
        sample_id = up_row.get("SAMPLE_ID", "")
        sample_loc = up_row.get("SAMPLE_LOC", "")
        page = int(up_row.get("PAGE_INDEX", "1"))

        strip = int(up_row.get("STRIP_INDEX", up_row.get("ROW", "0")))
        position = int(up_row.get("PATCH_INDEX", up_row.get("COL", "0")))

        if strip == 0 or position == 0:
            loc_strip, loc_pos = _parse_sample_loc(sample_loc)
            if loc_strip is not None:
                strip = loc_strip
            if loc_pos is not None:
                position = loc_pos

        if strip == 0:
            strip = 1
        if position == 0:
            position = 1

        device_keys = [key for key in up_row.keys() if key.startswith(("RGB_", "CMYK_"))]
        device_values = {k: read_float(up_row, k) for k in device_keys}

        patches.append(
            Patch(
                sample_id=sample_id,
                sample_loc=sample_loc,
                page=page,
                strip=strip,
                position=position,
                device_values=device_values,
            )
        )

    return patches


def group_by_page(patches: Sequence[Patch]) -> dict[int, list[Patch]]:
    pages: dict[int, list[Patch]] = {}
    for patch in patches:
        pages.setdefault(patch.page, []).append(patch)
    for page in pages.values():
        page.sort(key=lambda p: (p.strip, p.position))
    return pages


def _parse_sample_loc(sample_loc: str) -> tuple[int | None, int | None]:
    if not sample_loc:
        return None, None

    letters = "".join(ch for ch in sample_loc if ch.isalpha())
    digits = "".join(ch for ch in sample_loc if ch.isdigit())

    strip = _letters_to_number(letters) if letters else None
    position = int(digits) if digits else None
    return strip, position


def _letters_to_number(letters: str) -> int:
    value = 0
    for ch in letters.upper():
        if not ch.isalpha():
            continue
        value = value * 26 + (ord(ch) - ord("A") + 1)
    return max(1, value)
