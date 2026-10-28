"""Follower snapshots on disk and the changes between them."""

import sqlite3
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Literal

from herascrape import AsyncClient
from platformdirs import user_data_dir
from pydantic import BaseModel, computed_field

from .client import Usage
from .export import Row
from .instagram import (
    FOLLOWERS_PAGE_SIZE,
    Account,
    OnProgress,
    Profile,
    estimate_calls,
    walk_until_complete,
)
from .report import cap_note, footer, plural

SCHEMA_VERSION = 1
# Measured: a reshuffled list is complete after 3 to 5 passes.
TYPICAL_PASSES = 4
RECHECK_PASSES = 3
# Net change vs the profile's count change that is worth flagging.
MISMATCH_MIN = 3
MISMATCH_SHARE = 0.01
_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY,
    user_pk TEXT NOT NULL,
    username TEXT NOT NULL,
    taken_at TEXT NOT NULL,
    follower_count INTEGER NOT NULL,
    fetched INTEGER NOT NULL,
    complete INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS snapshots_by_user ON snapshots (user_pk, id);
CREATE TABLE IF NOT EXISTS snapshot_followers (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots (id) ON DELETE CASCADE,
    pk TEXT NOT NULL,
    username TEXT NOT NULL,
    full_name TEXT NOT NULL,
    is_private INTEGER NOT NULL,
    is_verified INTEGER NOT NULL,
    PRIMARY KEY (snapshot_id, pk)
);
"""

ChangeKind = Literal["followed", "unfollowed", "renamed"]


class Snapshot(BaseModel):
    id: int
    user_pk: str
    username: str
    taken_at: datetime
    follower_count: int
    fetched: int
    complete: bool


class FollowerChange(BaseModel):
    change: ChangeKind
    account: Account
    previous_username: str | None = None


class FollowerDiff(BaseModel):
    """The result of `track_followers`."""

    profile: Profile
    current: Snapshot
    previous: Snapshot | None
    changes: list[FollowerChange]
    changes_reported: bool
    passes: int
    usage: Usage | None = None

    @computed_field
    @property
    def count_mismatch(self) -> bool:
        """The lists' net change disagrees with the profile's count."""
        if self.previous is None or not self.changes_reported:
            return False
        observed = sum(
            {"followed": 1, "unfollowed": -1}.get(c.change, 0)
            for c in self.changes
        )
        expected = self.current.follower_count - self.previous.follower_count
        slack = max(MISMATCH_MIN, MISMATCH_SHARE * self.current.follower_count)
        return abs(expected - observed) > slack

    def csv_rows(self) -> list[Row]:
        return [
            {
                "change": c.change,
                "pk": c.account.pk,
                "username": c.account.username,
                "full_name": c.account.full_name,
                "previous_username": c.previous_username or "",
            }
            for c in self.changes
        ]

    def markdown(self) -> str:
        return _markdown(self)


def default_db_path() -> Path:
    return Path(user_data_dir("instagram-scraper", appauthor=False)) / (
        "followers.sqlite3"
    )


class SnapshotStore:
    """Follower snapshots in one SQLite file."""

    def __init__(self, *, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.execute("PRAGMA foreign_keys = ON")
        (version,) = self._conn.execute("PRAGMA user_version").fetchone()
        if version > SCHEMA_VERSION:
            self._conn.close()
            raise ValueError(
                f"{path} was written by a newer version of instagram-scraper"
            )
        self._conn.executescript(_SCHEMA)
        self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def __enter__(self) -> "SnapshotStore":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._conn.close()

    def save(
        self,
        *,
        profile: Profile,
        accounts: Sequence[Account],
        complete: bool,
        taken_at: datetime,
    ) -> Snapshot:
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO snapshots (user_pk, username, taken_at,"
                " follower_count, fetched, complete) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    profile.pk,
                    profile.username,
                    taken_at.isoformat(),
                    profile.follower_count,
                    len(accounts),
                    complete,
                ),
            )
            snapshot_id = cursor.lastrowid or 0
            self._conn.executemany(
                "INSERT OR IGNORE INTO snapshot_followers (snapshot_id, pk,"
                " username, full_name, is_private, is_verified)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        snapshot_id,
                        a.pk,
                        a.username,
                        a.full_name,
                        a.is_private,
                        a.is_verified,
                    )
                    for a in accounts
                ],
            )
        return Snapshot(
            id=snapshot_id,
            user_pk=profile.pk,
            username=profile.username,
            taken_at=taken_at,
            follower_count=profile.follower_count,
            fetched=len(accounts),
            complete=complete,
        )

    def latest_complete(self, *, user_pk: str) -> Snapshot | None:
        """The newest snapshot of `user_pk` that saw the whole list."""
        row = self._conn.execute(
            "SELECT id, user_pk, username, taken_at, follower_count, fetched,"
            " complete FROM snapshots WHERE user_pk = ? AND complete = 1"
            " ORDER BY id DESC LIMIT 1",
            (user_pk,),
        ).fetchone()
        if row is None:
            return None
        return Snapshot(
            id=row[0],
            user_pk=row[1],
            username=row[2],
            taken_at=datetime.fromisoformat(row[3]),
            follower_count=row[4],
            fetched=row[5],
            complete=bool(row[6]),
        )

    def accounts(self, *, snapshot_id: int) -> list[Account]:
        rows = self._conn.execute(
            "SELECT pk, username, full_name, is_private, is_verified"
            " FROM snapshot_followers WHERE snapshot_id = ? ORDER BY rowid",
            (snapshot_id,),
        ).fetchall()
        return [
            Account(
                pk=pk,
                username=username,
                full_name=full_name,
                is_private=bool(is_private),
                is_verified=bool(is_verified),
            )
            for pk, username, full_name, is_private, is_verified in rows
        ]


def diff_accounts(
    *, previous: Sequence[Account], current: Sequence[Account]
) -> list[FollowerChange]:
    """Followed, then unfollowed, then renamed; each in list order."""
    before = {a.pk: a for a in previous}
    now = {a.pk: a for a in current}
    changes = [
        FollowerChange(change="followed", account=a)
        for a in current
        if a.pk not in before
    ]
    changes += [
        FollowerChange(change="unfollowed", account=a)
        for a in previous
        if a.pk not in now
    ]
    changes += [
        FollowerChange(
            change="renamed",
            account=a,
            previous_username=before[a.pk].username,
        )
        for a in current
        if a.pk in before and before[a.pk].username != a.username
    ]
    return changes


def estimate_follower_calls(profile: Profile) -> int:
    pages = estimate_calls(
        items=profile.follower_count, page_size=FOLLOWERS_PAGE_SIZE
    )
    return pages * TYPICAL_PASSES


async def track_followers(
    client: AsyncClient,
    *,
    profile: Profile,
    store: SnapshotStore,
    on_progress: OnProgress | None = None,
) -> FollowerDiff:
    """Save a full follower snapshot and diff it with the last complete one.

    Instagram reorders follower lists between requests, so the list is read
    in repeated passes until it stops growing, and anyone who seems to have
    unfollowed is looked for again before being reported.
    """
    walk = await walk_until_complete(
        client,
        edge="followers",
        user_pk=profile.pk,
        expected=profile.follower_count,
        on_progress=on_progress,
    )
    previous = store.latest_complete(user_pk=profile.pk)
    accounts, passes = walk.accounts, walk.passes
    before: list[Account] = []
    comparable = previous is not None and walk.complete
    if previous is not None and comparable:
        before = store.accounts(snapshot_id=previous.id)
        missing = {a.pk for a in before} - {a.pk for a in accounts}
        if missing:
            recheck = await walk_until_complete(
                client,
                edge="followers",
                user_pk=profile.pk,
                expected=profile.follower_count,
                max_passes=RECHECK_PASSES,
                stop_when=lambda pks: missing <= pks,
                on_progress=on_progress,
            )
            passes += recheck.passes
            accounts = accounts + [
                a for a in recheck.accounts if a.pk in missing
            ]
    current = store.save(
        profile=profile,
        accounts=accounts,
        complete=walk.complete,
        taken_at=datetime.now(timezone.utc),
    )
    return FollowerDiff(
        profile=profile,
        current=current,
        previous=previous,
        changes=(
            diff_accounts(previous=before, current=accounts)
            if comparable
            else []
        ),
        changes_reported=comparable,
        passes=passes,
    )


def _section(title: str, changes: Sequence[FollowerChange]) -> list[str]:
    if not changes:
        return []
    lines = ["", f"## {title} ({len(changes):,})", ""]
    for c in changes:
        name = f" ({c.account.full_name})" if c.account.full_name else ""
        if c.previous_username:
            lines.append(f"- @{c.previous_username} → @{c.account.username}")
        else:
            lines.append(f"- @{c.account.username}{name}")
    return lines


def _markdown(diff: FollowerDiff) -> str:
    profile, current = diff.profile, diff.current
    passes = plural(count=diff.passes, word="pass", many="passes")
    listed = plural(count=current.fetched, word="follower")
    lines = [
        f"# Follower changes: @{profile.username}",
        "",
        f"Snapshot {current.taken_at:%Y-%m-%d %H:%M} UTC: "
        f"{current.fetched:,} of {current.follower_count:,} followers, read "
        f"in {passes} (Instagram reorders the "
        "list between requests, so it is read until it stops growing).",
    ]
    if not current.complete:
        lines += [
            "",
            f"> {cap_note(profile=profile, listed=current.fetched)} Without "
            "the full list, follower changes can't be tracked.",
        ]
    if diff.previous is None or not diff.changes_reported:
        if current.complete:
            lines += [
                "",
                f"Baseline saved: {listed}. Run it again later to see who "
                "followed and unfollowed.",
            ]
        lines += ["", footer()]
        return "\n".join(lines) + "\n"
    since = f"{diff.previous.taken_at:%Y-%m-%d %H:%M} UTC"
    by_kind = {
        kind: [c for c in diff.changes if c.change == kind]
        for kind in ("followed", "unfollowed", "renamed")
    }
    lines += [
        "",
        f"Since {since}: {len(by_kind['followed']):,} new, "
        f"{len(by_kind['unfollowed']):,} unfollowed, "
        f"{len(by_kind['renamed']):,} renamed.",
    ]
    if diff.count_mismatch:
        net = diff.current.follower_count - diff.previous.follower_count
        lines += [
            "",
            f"> The profile's follower count changed by {net:+,}, but the "
            "lists differ by more than that, so some changes may be missing "
            "or false: Instagram does not always list every follower.",
        ]
    lines += _section("New followers", by_kind["followed"])
    lines += _section("Unfollowed", by_kind["unfollowed"])
    lines += _section("Renamed", by_kind["renamed"])
    lines += ["", footer()]
    return "\n".join(lines) + "\n"
