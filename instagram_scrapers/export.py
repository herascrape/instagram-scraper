"""Result writers, picked by file extension."""

import csv
import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

Row = dict[str, str | int | float | None]
Cell = str | int | float | None
# Text starting with these is read as a formula by Excel and Sheets.
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


class Exportable(Protocol):
    """What every command result offers the writers."""

    def csv_rows(self) -> list[Row]: ...

    def markdown(self) -> str: ...

    def model_dump_json(self, *, indent: int | None = None) -> str: ...


def write_csv(result: Exportable, path: Path) -> None:
    rows = result.csv_rows()
    # utf-8-sig so Excel shows non-ASCII names correctly.
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        if not rows:
            return
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows({k: _safe(v) for k, v in row.items()} for row in rows)


def _safe(value: Cell) -> Cell:
    if isinstance(value, str) and value.startswith(_FORMULA_START):
        return "'" + value
    return value


def write_json(result: Exportable, path: Path) -> None:
    path.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")


def write_markdown(result: Exportable, path: Path) -> None:
    path.write_text(result.markdown(), encoding="utf-8")


WRITERS: dict[str, Callable[[Exportable, Path], None]] = {
    ".csv": write_csv,
    ".json": write_json,
    ".md": write_markdown,
}


def check_outputs(paths: Sequence[Path]) -> None:
    """Reject a bad format or folder before any API call is made."""
    supported = ", ".join(WRITERS)
    for path in paths:
        if path.suffix.lower() not in WRITERS:
            raise ValueError(
                f"{path.name}: unsupported output format; use {supported}"
            )
        folder = path.parent
        if not folder.is_dir():
            raise ValueError(f"{path}: the folder {folder} doesn't exist")
        if not os.access(folder, os.W_OK):
            raise ValueError(f"{path}: can't write to {folder}")


def write_outputs(result: Exportable, *, paths: Sequence[Path]) -> None:
    check_outputs(paths)
    for path in paths:
        WRITERS[path.suffix.lower()](result, path)
