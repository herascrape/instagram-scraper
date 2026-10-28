"""Result writers, picked by file extension."""

import csv
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

Row = dict[str, str | int | float | None]


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
        writer.writerows(rows)


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
    """Reject an unsupported extension before any API call is made."""
    supported = ", ".join(WRITERS)
    for path in paths:
        if path.suffix.lower() not in WRITERS:
            raise ValueError(
                f"{path.name}: unsupported output format; use {supported}"
            )


def write_outputs(result: Exportable, *, paths: Sequence[Path]) -> None:
    check_outputs(paths)
    for path in paths:
        WRITERS[path.suffix.lower()](result, path)
