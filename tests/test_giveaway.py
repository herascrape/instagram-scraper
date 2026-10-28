import hashlib
from datetime import datetime, timezone
from typing import cast

import pytest

from instagram_scrapers.client import open_client
from instagram_scrapers.giveaway import (
    GiveawayResult,
    GiveawayRules,
    Ticket,
    UnknownAccountError,
    entries_digest,
    estimate_giveaway_calls,
    rank_key,
    run_giveaway,
)
from instagram_scrapers.instagram import (
    JSON,
    Account,
    PostInfo,
    extract_mentions,
)
from tests.factories import (
    comment,
    comments_page,
    compact_user,
    full_profile,
    list_page,
)
from tests.fakeapi import FakeAPI

HOST = compact_user("9", "bakery")
POST = PostInfo(
    pk="77",
    code="ABC",
    url="https://www.instagram.com/p/ABC/",
    owner=Account.from_api(HOST),
    coauthors=[Account.from_api(compact_user("8", "partner"))],
    comment_count=50,
    taken_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
    caption="Giveaway",
)


def _api(comments: list[JSON]) -> FakeAPI:
    api = FakeAPI()
    api.route("/posts/77/comments", comments_page(comments, next_min_id=None))
    return api


async def _draw(
    api: FakeAPI, rules: GiveawayRules, *, seed: str = "seed"
) -> GiveawayResult:
    client, _ = open_client(api_key="hs_test", inner=api.transport())
    async with client:
        return await run_giveaway(client, post=POST, rules=rules, seed=seed)


def _ranked(seed: str, comment_pks: list[str]) -> list[str]:
    return sorted(
        comment_pks, key=lambda pk: rank_key(seed=seed, comment_pk=pk)
    )


def _pks(comments: list[JSON]) -> list[str]:
    return [str(c["pk"]) for c in comments]


def _fans(count: int) -> list[JSON]:
    return [
        comment(
            str(100 + n),
            user=compact_user(str(1000 + n), f"fan{n}"),
            text="me!",
            created_at=1_790_000_000 + n,
        )
        for n in range(count)
    ]


def test_extract_mentions_edge_cases() -> None:
    text = "@jane.doe_ and @Bob!! @jane.doe_ 🎉@max"
    assert extract_mentions(text) == ["jane.doe_", "bob", "max"]
    assert extract_mentions("thanks @ann.") == ["ann"]
    assert extract_mentions("mail a@b.com") == []


def test_digest_is_order_independent_and_known() -> None:
    def ticket(comment_pk: str, user_pk: str) -> Ticket:
        return Ticket(
            comment_pk=comment_pk,
            user=Account(pk=user_pk, username=f"u{user_pk}"),
            text="",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            mentions=[],
            eligible=True,
        )

    a, b = ticket("20", "2"), ticket("10", "1")
    expected = hashlib.sha256(b"10,1\n20,2\n").hexdigest()
    assert entries_digest([a, b]) == expected
    assert entries_digest([b, a]) == expected


def test_rank_key_known_value() -> None:
    assert rank_key(seed="s", comment_pk="10") == (
        hashlib.sha256(b"s:10").hexdigest()
    )


def test_estimate_giveaway_calls() -> None:
    assert estimate_giveaway_calls(post=POST, rules=GiveawayRules()) == 1 + 4
    checked = GiveawayRules(must_follow=["bakery"], max_check_calls=300)
    assert estimate_giveaway_calls(post=POST, rules=checked) == 1 + 4 + 300


async def test_filters() -> None:
    fan = compact_user("1", "fan")
    early = compact_user("2", "early")
    api = _api(
        [
            comment("10", user=HOST, text="Good luck @a @b #win"),
            comment("11", user=compact_user("8", "partner"), text="@a @b #win"),
            comment("12", user=compact_user("3", "spam"), text="@a @b #win"),
            comment(
                "13", user=fan, text="@a @b #WIN", created_at=1_790_000_001
            ),
            comment(
                "14", user=fan, text="@a @c #win", created_at=1_790_000_002
            ),
            comment(
                "15", user=fan, text="@a @d #win", created_at=1_790_000_003
            ),
            comment("16", user=early, text="@a #win"),
            comment("17", user=early, text="@a @bakery @early #win"),
            comment("18", user=early, text="@a @b no tag"),
            comment(
                "19",
                user=compact_user("4", "late"),
                text="@a @b #win",
                created_at=1_800_000_000,
            ),
        ]
    )
    rules = GiveawayRules(
        min_mentions=2,
        keywords=["#win"],
        until=datetime(2026, 10, 1, tzinfo=timezone.utc),
        max_entries_per_user=2,
        exclude=["spam"],
    )
    result = await _draw(api, rules)
    reasons = {t.comment_pk: t.reason for t in result.tickets}
    assert reasons == {
        "10": "host",
        "11": "co-author",
        "12": "excluded",
        "13": None,
        "14": None,
        "15": "entry limit (2) reached",
        "16": "1 of 2 mentions",
        "17": "1 of 2 mentions",
        "18": "missing #win",
        "19": "after close",
    }
    assert result.eligible_users == 1
    assert result.comments_fetched == 10


async def test_draw_is_reproducible() -> None:
    comments = _fans(20)
    first = await _draw(_api(comments), GiveawayRules(winners=3), seed="x1")
    again = await _draw(_api(comments), GiveawayRules(winners=3), seed="x1")
    other = await _draw(_api(comments), GiveawayRules(winners=3), seed="x2")

    def picks(result: GiveawayResult) -> list[str]:
        return [c.ticket.comment_pk for c in result.winners]

    assert picks(first) == picks(again)
    assert picks(first) == _ranked("x1", _pks(comments))[:3]
    assert picks(first) != picks(other)
    assert first.entries_digest == again.entries_digest == other.entries_digest


async def test_one_win_per_user() -> None:
    twice = compact_user("1", "twice")
    comments = [
        comment(str(pk), user=twice, text="me", created_at=1_790_000_000 + pk)
        for pk in (10, 11)
    ] + [
        comment(str(pk), user=compact_user(str(pk), f"u{pk}"), text="me")
        for pk in range(20, 40)
    ]
    pks = _pks(comments)
    seed = next(
        f"s{n}"
        for n in range(10_000)
        if set(_ranked(f"s{n}", pks)[:2]) == {"10", "11"}
    )
    rules = GiveawayRules(winners=2, backups=0, max_entries_per_user=2)
    result = await _draw(_api(comments), rules, seed=seed)
    winners = [c.ticket.user.username for c in result.winners]
    assert winners[0] == "twice"
    assert winners[1] != "twice"
    assert len(winners) == 2


async def test_expensive_checks_only_on_candidates() -> None:
    comments = _fans(50)
    first, second = _ranked("seed", _pks(comments))[:2]
    user_of = {str(c["pk"]): cast(JSON, c["user"]) for c in comments}
    api = _api(comments)
    for pk, followers in ((first, 12), (second, 500)):
        user = user_of[pk]
        api.route(
            f"/users/{user['pk']}",
            full_profile(
                str(user["pk"]), str(user["username"]), followers=followers
            ),
        )
    rules = GiveawayRules(winners=1, backups=0, min_followers=100)
    result = await _draw(api, rules)
    assert [c.ticket.comment_pk for c in result.winners] == [second]
    assert [(c.ticket.comment_pk, c.reason) for c in result.rejected] == [
        (first, "followers 12 < 100")
    ]
    profile_calls = [p for p in api.paths("/users/") if p.count("/") == 2]
    assert len(profile_calls) == 2


async def test_must_follow_private_large_and_found() -> None:
    comments = _fans(10)
    order = _ranked("seed", _pks(comments))
    user_of = {str(c["pk"]): cast(JSON, c["user"]) for c in comments}
    private, large, follower = (user_of[pk] for pk in order[:3])
    api = _api(comments)
    api.route(
        f"/users/{private['pk']}",
        full_profile(str(private["pk"]), is_private=True),
    )
    api.route(
        f"/users/{large['pk']}",
        full_profile(str(large["pk"]), following=5000),
    )
    api.route(
        f"/users/{follower['pk']}",
        full_profile(str(follower["pk"]), following=40),
    )
    path = f"/users/{follower['pk']}/following"
    api.route(path, list_page([compact_user("50")], next_cursor="f1"))
    api.route(
        path,
        list_page([compact_user("9", "bakery")], next_cursor="f2"),
        query={"cursor": "f1"},
    )
    rules = GiveawayRules(
        winners=1,
        backups=0,
        must_follow=["bakery"],
        max_following_to_scan=50,
    )
    result = await _draw(api, rules)
    assert [c.ticket.user.pk for c in result.winners] == [follower["pk"]]
    assert [c.reason for c in result.rejected] == [
        "private, can't verify follows",
        "follows 5,000 accounts, more than the 50 checked",
    ]
    assert api.paths(f"/users/{large['pk']}/following") == []
    assert api.paths(path) == [path, path]


async def test_must_follow_found_on_a_later_pass() -> None:
    comments = _fans(1)
    user = cast(JSON, comments[0]["user"])
    api = _api(comments)
    api.route(
        f"/users/{user['pk']}", full_profile(str(user["pk"]), following=3)
    )
    api.route(
        f"/users/{user['pk']}/following",
        list_page([compact_user("50"), compact_user("51")], next_cursor=None),
        list_page([compact_user("51"), compact_user("9")], next_cursor=None),
    )
    rules = GiveawayRules(winners=1, backups=0, must_follow=["bakery"])
    result = await _draw(api, rules)
    assert [c.ticket.user.pk for c in result.winners] == [user["pk"]]


async def test_must_follow_missing() -> None:
    comments = _fans(1)
    user = cast(JSON, comments[0]["user"])
    api = _api(comments)
    api.route(
        f"/users/{user['pk']}", full_profile(str(user["pk"]), following=3)
    )
    api.route(
        f"/users/{user['pk']}/following",
        list_page(
            [compact_user("50"), compact_user("51"), compact_user("52")],
            next_cursor=None,
        ),
    )
    rules = GiveawayRules(winners=1, backups=0, must_follow=["bakery"])
    result = await _draw(api, rules)
    assert result.winners == []
    assert [c.reason for c in result.rejected] == ["doesn't follow @bakery"]


async def test_markdown_and_csv() -> None:
    result = await _draw(_api(_fans(5)), GiveawayRules(winners=1))
    text = result.markdown()
    assert result.seed in text
    assert result.entries_digest in text
    assert "sha256" in text
    assert "1 winner and 2 backups; one win per account" in text
    rows = result.csv_rows()
    assert len(rows) == 5
    assert sum(1 for r in rows if r["result"] == "winner") == 1
    assert sum(1 for r in rows if r["result"] == "backup") == 2


async def test_random_seed_when_none() -> None:
    client, _ = open_client(api_key="hs_test", inner=_api(_fans(3)).transport())
    async with client:
        result = await run_giveaway(client, post=POST, rules=GiveawayRules())
    assert len(result.seed) == 32


def test_naive_until_is_utc() -> None:
    rules = GiveawayRules(until=datetime(2026, 10, 1, 18, 0))
    assert rules.until == datetime(2026, 10, 1, 18, 0, tzinfo=timezone.utc)


async def test_rejected_account_is_not_checked_twice() -> None:
    fan = compact_user("1", "fan")
    comments = [
        comment(str(pk), user=fan, text="me", created_at=1_790_000_000 + pk)
        for pk in (10, 11)
    ]
    api = _api(comments)
    api.route("/users/1", full_profile("1", "fan", followers=5))
    rules = GiveawayRules(
        winners=1, backups=0, max_entries_per_user=2, min_followers=100
    )
    result = await _draw(api, rules)
    assert result.winners == []
    assert len(result.rejected) == 1
    assert api.paths("/users/1") == ["/users/1"]


async def test_markdown_publishes_seed_hash_and_scan_limit() -> None:
    rules = GiveawayRules(winners=1, backups=0, must_follow=["bakery"])
    result = await _draw(_api([]), rules, seed="s")
    text = result.markdown()
    assert hashlib.sha256(b"s").hexdigest() in text
    assert result.seed_sha256 == hashlib.sha256(b"s").hexdigest()
    assert (
        "Winner follows @bakery; drawn accounts that follow more than 2,000 "
        "accounts are skipped"
    ) in text


async def test_check_spend_is_capped() -> None:
    comments = _fans(10)
    api = _api(comments)
    for c in comments:
        user = cast(JSON, c["user"])
        api.route(
            f"/users/{user['pk']}", full_profile(str(user["pk"]), followers=5)
        )
    rules = GiveawayRules(
        winners=1, backups=0, min_followers=100, max_check_calls=3
    )
    result = await _draw(api, rules)
    assert result.winners == []
    assert result.check_calls == 3
    assert result.checks_stopped is True
    assert len([p for p in api.paths("/users/") if p.count("/") == 2]) == 3
    assert "Stopped checking drawn accounts after 3 calls" in result.markdown()


async def test_unknown_must_follow_fails_before_fetching_comments() -> None:
    api = _api(_fans(3))
    api.error(
        "/users/username/nobody", status=404, error_code="profile_not_found"
    )
    rules = GiveawayRules(must_follow=["nobody"])
    with pytest.raises(UnknownAccountError, match="nobody"):
        await _draw(api, rules)
    assert api.paths("/posts/") == []
