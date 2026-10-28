import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from instagram_scrapers.client import open_client
from instagram_scrapers.followers import (
    FollowerDiff,
    SnapshotStore,
    diff_accounts,
    estimate_follower_calls,
    track_followers,
)
from instagram_scrapers.instagram import JSON, Account, Profile
from tests.factories import compact_user, full_profile, list_page
from tests.fakeapi import FakeAPI

T0 = datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 10, 28, 9, 0, tzinfo=timezone.utc)


def _accounts(*names: str) -> list[Account]:
    return [Account(pk=f"pk-{n}", username=n) for n in names]


def _profile(followers: int, *, account_type: int = 1) -> Profile:
    return Profile.from_api(
        full_profile(
            "9", "bakery", followers=followers, account_type=account_type
        )
    )


def test_store_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "f.sqlite3"
    with SnapshotStore(path=path) as store:
        first = store.save(
            profile=_profile(2),
            accounts=_accounts("a", "b"),
            complete=True,
            taken_at=T0,
        )
        second = store.save(
            profile=_profile(1),
            accounts=_accounts("a"),
            complete=False,
            taken_at=T1,
        )
        assert store.latest_complete(user_pk="9") == first
        assert store.latest_complete(user_pk="other") is None
        assert [a.username for a in store.accounts(snapshot_id=first.id)] == [
            "a",
            "b",
        ]
    assert second.complete is False
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone() == (1,)


def test_store_rejects_newer_schema(tmp_path: Path) -> None:
    path = tmp_path / "f.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA user_version = 99")
    with pytest.raises(ValueError, match="newer version"):
        SnapshotStore(path=path)


def test_diff_followed_unfollowed_renamed() -> None:
    previous = _accounts("stays", "leaves", "old.name")
    current = [
        *_accounts("stays", "joins"),
        Account(pk="pk-old.name", username="new.name"),
    ]
    changes = diff_accounts(previous=previous, current=current)
    assert [
        (c.change, c.account.username, c.previous_username) for c in changes
    ] == [
        ("followed", "joins", None),
        ("unfollowed", "leaves", None),
        ("renamed", "new.name", "old.name"),
    ]


def _api(names: list[str]) -> FakeAPI:
    api = FakeAPI()
    items = [compact_user(f"pk-{n}", n) for n in names]
    api.route("/users/9/followers", list_page(items, next_cursor=None))
    return api


async def _track(
    api: FakeAPI, store: SnapshotStore, *, profile: Profile
) -> FollowerDiff:
    client, _ = open_client(api_key="hs_test", inner=api.transport())
    async with client:
        return await track_followers(client, profile=profile, store=store)


async def test_first_run_baseline(tmp_path: Path) -> None:
    with SnapshotStore(path=tmp_path / "f.sqlite3") as store:
        diff = await _track(_api(["a", "b"]), store, profile=_profile(2))
    assert diff.previous is None
    assert diff.changes == []
    assert diff.current.complete is True
    assert "Baseline saved: 2 followers" in diff.markdown()


async def test_second_run_reports_changes(tmp_path: Path) -> None:
    with SnapshotStore(path=tmp_path / "f.sqlite3") as store:
        await _track(_api(["a", "b"]), store, profile=_profile(2))
        diff = await _track(_api(["b", "c"]), store, profile=_profile(2))
    assert diff.changes_reported is True
    assert sorted((c.change, c.account.username) for c in diff.changes) == [
        ("followed", "c"),
        ("unfollowed", "a"),
    ]
    text = diff.markdown()
    assert "@c" in text and "@a" in text
    assert [r["change"] for r in diff.csv_rows()] == ["followed", "unfollowed"]


async def test_incomplete_snapshot_has_no_changes(tmp_path: Path) -> None:
    brand = _profile(268_000_000, account_type=2)
    with SnapshotStore(path=tmp_path / "f.sqlite3") as store:
        await _track(_api(["a", "b"]), store, profile=brand)
        diff = await _track(_api(["b", "c"]), store, profile=brand)
    assert diff.current.complete is False
    assert diff.changes_reported is False
    assert diff.changes == []
    assert diff.passes == 1
    assert "changes can't be tracked" in diff.markdown()


async def test_reshuffled_list_is_read_until_complete(tmp_path: Path) -> None:
    api = FakeAPI()
    first = [compact_user("pk-a", "a"), compact_user("pk-b", "b")]
    second = [compact_user("pk-b", "b"), compact_user("pk-c", "c")]
    api.route(
        "/users/9/followers",
        list_page(first, next_cursor=None),
        list_page(second, next_cursor=None),
    )
    with SnapshotStore(path=tmp_path / "f.sqlite3") as store:
        diff = await _track(api, store, profile=_profile(3))
    assert diff.current.fetched == 3
    assert diff.current.complete is True
    assert diff.passes == 2
    assert "2 passes" in diff.markdown()


@pytest.mark.parametrize(("listed", "complete"), [(98, True), (97, False)])
async def test_complete_threshold(
    tmp_path: Path, listed: int, complete: bool
) -> None:
    names = [f"u{n}" for n in range(listed)]
    with SnapshotStore(path=tmp_path / "f.sqlite3") as store:
        diff = await _track(_api(names), store, profile=_profile(100))
    assert diff.current.complete is complete


def test_estimate_follower_calls() -> None:
    assert estimate_follower_calls(_profile(10_000)) == 1_600
    assert estimate_follower_calls(_profile(100)) == 16


def _names(n: int, *, skip: str | None = None) -> list[JSON]:
    return [
        compact_user(f"pk-u{i}", f"u{i}") for i in range(n) if f"u{i}" != skip
    ]


async def test_missing_follower_is_rechecked_before_reporting(
    tmp_path: Path,
) -> None:
    api = FakeAPI()
    api.route(
        "/users/9/followers",
        list_page(_names(50), next_cursor=None),
        list_page(_names(50, skip="u49"), next_cursor=None),
        list_page(_names(50, skip="u49"), next_cursor=None),
        list_page(_names(50), next_cursor=None),
    )
    with SnapshotStore(path=tmp_path / "f.sqlite3") as store:
        await _track(api, store, profile=_profile(50))
        diff = await _track(api, store, profile=_profile(50))
        saved = store.accounts(snapshot_id=diff.current.id)
    assert diff.changes == []
    assert diff.current.fetched == 50
    assert "pk-u49" in {a.pk for a in saved}


async def test_follower_still_missing_after_rechecks_is_reported(
    tmp_path: Path,
) -> None:
    api = FakeAPI()
    api.route(
        "/users/9/followers",
        list_page(_names(50), next_cursor=None),
        list_page(_names(50, skip="u49"), next_cursor=None),
    )
    with SnapshotStore(path=tmp_path / "f.sqlite3") as store:
        await _track(api, store, profile=_profile(50))
        diff = await _track(api, store, profile=_profile(49))
    assert [(c.change, c.account.username) for c in diff.changes] == [
        ("unfollowed", "u49")
    ]


async def test_diff_uses_the_latest_complete_snapshot(tmp_path: Path) -> None:
    api = FakeAPI()
    api.route(
        "/users/9/followers",
        list_page(_names(3), next_cursor=None),
        list_page(_names(1), next_cursor=None),
        list_page(_names(3, skip="u0"), next_cursor=None),
    )
    with SnapshotStore(path=tmp_path / "f.sqlite3") as store:
        first = await _track(api, store, profile=_profile(3))
        partial = await _track(api, store, profile=_profile(3))
        third = await _track(api, store, profile=_profile(2))
    assert partial.current.complete is False
    assert third.previous == first.current
    assert [(c.change, c.account.username) for c in third.changes] == [
        ("unfollowed", "u0")
    ]


async def test_count_mismatch_is_flagged(tmp_path: Path) -> None:
    steady = FakeAPI()
    steady.route("/users/9/followers", list_page(_names(100), next_cursor=None))
    with SnapshotStore(path=tmp_path / "f.sqlite3") as store:
        await _track(steady, store, profile=_profile(100))
        diff = await _track(steady, store, profile=_profile(98))
    assert diff.count_mismatch is False

    api = FakeAPI()
    api.route(
        "/users/9/followers",
        list_page(_names(980), next_cursor=None),
        list_page(_names(980), next_cursor=None),
        list_page(_names(1000), next_cursor=None),
    )
    with SnapshotStore(path=tmp_path / "g.sqlite3") as store:
        await _track(api, store, profile=_profile(1000))
        diff = await _track(api, store, profile=_profile(1000))
    assert len(diff.changes) == 20
    assert diff.count_mismatch is True
    assert "some changes may be missing or false" in diff.markdown()
