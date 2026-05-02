from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List
import shlex


@dataclass(slots=True)
class CGATS:
    keywords: dict[str, str]
    data_format: list[str]
    data: list[dict[str, str]]

    def field_names(self) -> List[str]:
        return list(self.data_format)


def load(path: str | Path) -> CGATS:
    path = Path(path)
    keywords: dict[str, str] = {}
    data_format: list[str] = []
    data_rows: list[list[str]] = []

    state: str | None = None  # "format" | "data"

    for raw_line in path.read_text().splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue

        upper = line.upper()
        if upper == "BEGIN_DATA_FORMAT":
            state = "format"
            continue
        if upper == "END_DATA_FORMAT":
            state = None
            continue
        if upper == "BEGIN_DATA":
            state = "data"
            continue
        if upper == "END_DATA":
            state = None
            continue

        if state == "format":
            data_format.extend(_tokenize(line))
            continue
        if state == "data":
            tokens = _tokenize(line)
            if len(tokens) != len(data_format):
                raise ValueError(
                    f"Row has {len(tokens)} fields, expected {len(data_format)}: {line}"
                )
            data_rows.append(tokens)
            continue

        # top-level keyword line
        tokens = _tokenize(line)
        if len(tokens) == 1:
            keywords[tokens[0]] = ""
        elif len(tokens) >= 2:
            keywords[tokens[0]] = " ".join(tokens[1:])

    data = [dict(zip(data_format, row)) for row in data_rows]
    return CGATS(keywords=keywords, data_format=data_format, data=data)


def _tokenize(line: str) -> list[str]:
    lexer = shlex.shlex(line, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    return list(lexer)
