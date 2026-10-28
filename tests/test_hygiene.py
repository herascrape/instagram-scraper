import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
KEY_PATTERN = re.compile(r"hs_[A-Za-z0-9_-]{40,}")
LOCAL_PATH_PATTERN = re.compile(r"/(?:Users|home)/\w")


def _tracked_files() -> list[Path]:
    names = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split("\0")
    return [REPO_ROOT / name for name in names if name]


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def test_no_real_key_committed() -> None:
    found = [
        f"{path.relative_to(REPO_ROOT)}: {match[:8]}..."
        for path in _tracked_files()
        for match in KEY_PATTERN.findall(_text(path))
    ]
    assert found == []


def test_no_local_paths_committed() -> None:
    found = [
        str(path.relative_to(REPO_ROOT))
        for path in _tracked_files()
        if LOCAL_PATH_PATTERN.search(_text(path))
    ]
    assert found == []
