import csv
import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from instagram_scrapers.export import Row, check_outputs, write_outputs


class _Result(BaseModel):
    name: str

    def csv_rows(self) -> list[Row]:
        return [{"name": self.name, "count": 2}, {"name": "ü✓", "count": None}]

    def markdown(self) -> str:
        return f"# {self.name}\n"


def test_writes_by_suffix(tmp_path: Path) -> None:
    result = _Result(name="report")
    paths = [tmp_path / "r.csv", tmp_path / "r.json", tmp_path / "r.md"]
    write_outputs(result, paths=paths)
    with paths[0].open(encoding="utf-8-sig", newline="") as handle:
        assert list(csv.DictReader(handle)) == [
            {"name": "report", "count": "2"},
            {"name": "ü✓", "count": ""},
        ]
    assert json.loads(paths[1].read_text()) == {"name": "report"}
    assert paths[2].read_text() == "# report\n"


def test_unknown_suffix_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"\.csv, \.json, \.md"):
        check_outputs([tmp_path / "r.xlsx"])


def test_empty_rows_write_an_empty_csv(tmp_path: Path) -> None:
    class Empty(_Result):
        def csv_rows(self) -> list[Row]:
            return []

    path = tmp_path / "r.csv"
    write_outputs(Empty(name="x"), paths=[path])
    assert path.read_text(encoding="utf-8-sig") == ""
