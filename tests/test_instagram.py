from collections.abc import Callable
from datetime import datetime, timezone

import pytest

from instagram_scrapers.client import open_client
from instagram_scrapers.instagram import (
    JSON,
    Profile,
    Walk,
    fetch_comments,
    fetch_followers,
    fetch_posts,
    get_post,
    get_profile,
    parse_post_ref,
    parse_username,
    walk_until_complete,
)
from tests.factories import (
    comment,
    comments_page,
    compact_user,
    full_profile,
    grid_page,
    list_page,
    post_body,
    post_detail,
)
from tests.fakeapi import FakeAPI


@pytest.mark.parametrize(
    "value",
    [
        "natgeo",
        "@natgeo",
        " natgeo ",
        "https://www.instagram.com/natgeo/",
        "instagram.com/natgeo?hl=en",
        "http://instagram.com/natgeo",
    ],
)
def test_parse_username_variants(value: str) -> None:
    assert parse_username(value) == "natgeo"


@pytest.mark.parametrize(
    "value", ["https://instagram.com/p/ABC/", "", "@", "bad name"]
)
def test_parse_username_rejects(value: str) -> None:
    with pytest.raises(ValueError):
        parse_username(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://www.instagram.com/p/DQSPu0hE_pP/", "DQSPu0hE_pP"),
        ("https://www.instagram.com/reel/DQSPu0hE_pP/?igsh=abc", "DQSPu0hE_pP"),
        ("instagram.com/reels/DQSPu0hE_pP", "DQSPu0hE_pP"),
        ("https://instagram.com/tv/B4XpqViUFUI", "B4XpqViUFUI"),
        ("https://www.instagram.com/natgeo/p/DQSPu0hE_pP/", "DQSPu0hE_pP"),
        ("DQSPu0hE_pP", "DQSPu0hE_pP"),
        ("2168384974851429640", "2168384974851429640"),
        ("2168384974851429640_25025320", "2168384974851429640_25025320"),
    ],
)
def test_parse_post_ref_variants(value: str, expected: str) -> None:
    assert parse_post_ref(value) == expected


@pytest.mark.parametrize(
    "value", ["https://www.instagram.com/natgeo/", "", "not a post!"]
)
def test_parse_post_ref_rejects(value: str) -> None:
    with pytest.raises(ValueError):
        parse_post_ref(value)


def _profile(followers: int) -> Profile:
    return Profile.from_api(full_profile("9", "host", followers=followers))


async def test_fetch_followers_capped() -> None:
    api = FakeAPI()
    page = [compact_user(str(i)) for i in range(45)]
    api.route("/users/9/followers", list_page(page, next_cursor=None))
    client, _ = open_client(api_key="hs_test", inner=api.transport())
    async with client:
        result = await fetch_followers(
            client, profile=_profile(268_000_000), limit=100
        )
    assert result.capped is True
    assert len(result.accounts) == 45


async def test_fetch_followers_complete() -> None:
    api = FakeAPI()
    sizes = [25, 25, 25, 25, 16]
    start = 0
    for i, size in enumerate(sizes):
        items = [compact_user(str(n)) for n in range(start, start + size)]
        start += size
        cursor = f"c{i + 1}" if i < len(sizes) - 1 else None
        query = {"cursor": f"c{i}"} if i else None
        api.route(
            "/users/9/followers",
            list_page(items, next_cursor=cursor),
            query=query,
        )
    client, counter = open_client(api_key="hs_test", inner=api.transport())
    async with client:
        result = await fetch_followers(
            client, profile=_profile(116), limit=None
        )
    assert result.capped is False
    assert len(result.accounts) == 116
    assert counter.calls == 5


async def test_fetch_followers_stops_at_limit() -> None:
    api = FakeAPI()
    first = [compact_user(str(n)) for n in range(25)]
    api.route("/users/9/followers", list_page(first, next_cursor="c1"))
    client, counter = open_client(api_key="hs_test", inner=api.transport())
    async with client:
        result = await fetch_followers(client, profile=_profile(5000), limit=10)
    assert len(result.accounts) == 10
    assert result.capped is False
    assert counter.calls == 1


async def test_fetch_followers_dedupes_overlapping_pages() -> None:
    api = FakeAPI()
    first = [compact_user(str(n)) for n in range(25)]
    second = [compact_user(str(n)) for n in range(20, 45)]
    api.route("/users/9/followers", list_page(first, next_cursor="c1"))
    api.route(
        "/users/9/followers",
        list_page(second, next_cursor="c2"),
        query={"cursor": "c1"},
    )
    client, counter = open_client(api_key="hs_test", inner=api.transport())
    async with client:
        result = await fetch_followers(client, profile=_profile(5000), limit=40)
    assert [a.pk for a in result.accounts] == [str(n) for n in range(40)]
    assert counter.calls == 2


async def test_fetch_followers_stops_on_a_page_with_nothing_new() -> None:
    api = FakeAPI()
    first = [compact_user(str(n)) for n in range(25)]
    api.route("/users/9/followers", list_page(first, next_cursor="c1"))
    api.route(
        "/users/9/followers",
        list_page(first, next_cursor="c2"),
        query={"cursor": "c1"},
    )
    client, counter = open_client(api_key="hs_test", inner=api.transport())
    async with client:
        result = await fetch_followers(
            client, profile=_profile(5000), limit=None
        )
    assert len(result.accounts) == 25
    assert result.capped is True
    assert counter.calls == 2


async def test_fetch_posts_joins_halves_skips_pins() -> None:
    api = FakeAPI()
    pinned = post_detail("1", taken_at=1_600_000_000)
    reel = post_detail(
        "2", product_type="clips", media_type=2, plays=900, caption="#a"
    )
    carousel = post_detail("3", product_type="carousel_container", media_type=8)
    video = post_detail("4", media_type=2)
    photo = post_detail("5")
    del photo["taken_at"]
    photo["1ltaken_at"] = 1_700_000_000
    api.route(
        "/users/9/posts",
        grid_page([pinned, reel, carousel], cursor="g1", pinned={"1"}),
    )
    api.route(
        "/users/9/posts",
        grid_page([video, photo], cursor="g2"),
        query={"cursor": "g1"},
    )
    client, _ = open_client(api_key="hs_test", inner=api.transport())
    async with client:
        posts = await fetch_posts(client, user_pk="9", limit=4)
    assert [p.pk for p in posts] == ["2", "3", "4", "5"]
    assert [p.kind for p in posts] == ["reel", "carousel", "video", "photo"]
    assert posts[0].play_count == 900
    assert posts[0].caption == "#a"
    assert posts[0].url == "https://www.instagram.com/reel/C2/"
    assert posts[3].taken_at == datetime.fromtimestamp(
        1_700_000_000, tz=timezone.utc
    )


async def test_fetch_posts_stops_when_no_more() -> None:
    api = FakeAPI()
    api.route("/users/9/posts", grid_page([post_detail("1")], cursor=None))
    client, counter = open_client(api_key="hs_test", inner=api.transport())
    async with client:
        posts = await fetch_posts(client, user_pk="9", limit=24)
    assert len(posts) == 1
    assert counter.calls == 1


async def test_fetch_comments_pages_until_has_more_false() -> None:
    api = FakeAPI()
    owner = compact_user("9", "host")
    api.route(
        "/posts/ABC",
        post_body("77", code="ABC", owner=owner, comment_count=3),
    )
    fan = compact_user("5", "fan")
    first = [
        comment("1", user=fan, text="hi"),
        comment("2", user=fan, text="yo"),
    ]
    api.route("/posts/77/comments", comments_page(first, next_min_id="m1"))
    api.route(
        "/posts/77/comments",
        comments_page([comment("3", user=fan, text="x")], next_min_id=None),
        query={"min_id": "m1"},
    )
    client, _ = open_client(api_key="hs_test", inner=api.transport())
    async with client:
        post = await get_post(client, ref="ABC")
        comments = await fetch_comments(client, post=post)
    assert post.owner.username == "host"
    assert post.url == "https://www.instagram.com/p/ABC/"
    assert [c.pk for c in comments] == ["1", "2", "3"]
    assert comments[0].user.username == "fan"


async def test_get_profile_parses_and_tolerates_missing_fields() -> None:
    api = FakeAPI()
    body = full_profile("9", "host", followers=10)
    for key in ("biography", "category", "bio_links", "external_url"):
        del body[key]
    api.route("/users/username/host", body)
    client, _ = open_client(api_key="hs_test", inner=api.transport())
    async with client:
        profile = await get_profile(client, username="host")
    assert profile.follower_count == 10
    assert profile.biography == ""
    assert profile.has_bio_links is False


def _pass(*pks: str) -> JSON:
    return list_page([compact_user(pk) for pk in pks], next_cursor=None)


async def _walk(
    api: FakeAPI,
    *,
    expected: int,
    max_passes: int = 5,
    stop_when: Callable[[set[str]], bool] | None = None,
) -> Walk:
    client, _ = open_client(api_key="hs_test", inner=api.transport())
    async with client:
        return await walk_until_complete(
            client,
            edge="followers",
            user_pk="9",
            expected=expected,
            max_passes=max_passes,
            stop_when=stop_when,
        )


async def test_walk_unions_passes_until_complete() -> None:
    api = FakeAPI()
    api.route("/users/9/followers", _pass("a", "b"), _pass("b", "c"))
    walk = await _walk(api, expected=3)
    assert sorted(a.pk for a in walk.accounts) == ["a", "b", "c"]
    assert (walk.passes, walk.complete) == (2, True)
    assert len(api.requests) == 2


async def test_walk_stops_when_a_pass_adds_nothing() -> None:
    api = FakeAPI()
    api.route(
        "/users/9/followers", _pass("a", "b", "c", "d", "e", "f"), _pass("a")
    )
    walk = await _walk(api, expected=10)
    assert (walk.passes, walk.complete, len(walk.accounts)) == (2, False, 6)


async def test_walk_stops_on_a_cut_list() -> None:
    api = FakeAPI()
    api.route("/users/9/followers", _pass(*[str(n) for n in range(48)]))
    walk = await _walk(api, expected=1_000_000)
    assert (walk.passes, walk.complete) == (1, False)


async def test_walk_stops_when_the_target_is_found() -> None:
    api = FakeAPI()
    api.route("/users/9/followers", _pass("a", "host"), _pass("b"))
    walk = await _walk(api, expected=50, stop_when=lambda pks: "host" in pks)
    assert walk.passes == 1


async def test_walk_respects_max_passes() -> None:
    api = FakeAPI()
    api.route(
        "/users/9/followers",
        _pass(*[str(n) for n in range(60)]),
        _pass("x1"),
        _pass("x2"),
        _pass("x3"),
    )
    walk = await _walk(api, expected=100, max_passes=3)
    assert (walk.passes, len(walk.accounts)) == (3, 62)
