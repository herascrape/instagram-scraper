from instagram_scrapers.client import open_client
from instagram_scrapers.fake_followers import check_fake_followers
from instagram_scrapers.instagram import JSON, Profile
from tests.factories import compact_user, full_profile, list_page
from tests.fakeapi import FakeAPI

TARGET = full_profile("9", "bakery", followers=4)


def _api(followers: list[JSON]) -> FakeAPI:
    api = FakeAPI()
    api.route(
        "/users/9/followers",
        list_page(
            [compact_user(str(f["pk"]), str(f["username"])) for f in followers],
            next_cursor=None,
        ),
    )
    for follower in followers:
        api.route(f"/users/{follower['pk']}", follower)
    return api


async def test_check_counts_and_reasons() -> None:
    api = _api(
        [
            full_profile("1", "real.person", followers=300, following=280),
            full_profile("2", "quiet_one", posts=0, biography=""),
            full_profile(
                "3",
                "user84629173",
                followers=3,
                following=2500,
                posts=0,
                full_name="",
            ),
        ]
    )
    api.route(
        "/users/9/followers",
        list_page(
            [
                compact_user("1", "real.person"),
                compact_user("2", "quiet_one"),
                compact_user("3", "user84629173"),
                compact_user("4", "deleted.since"),
            ],
            next_cursor=None,
        ),
    )
    client, counter = open_client(api_key="hs_test", inner=api.transport())
    async with client:
        report = await check_fake_followers(
            client, target=Profile.from_api(TARGET), sample=100
        )
    assert report.counts == {"real": 1, "ghost": 1, "fake": 1}
    assert report.unavailable == 1
    assert report.signal_counts["no_posts"] == 2
    assert report.signal_counts["follow_ratio"] == 1
    by_name = {f.account.username: f for f in report.followers}
    assert by_name["user84629173"].category == "fake"
    assert "generated_username" in by_name["user84629173"].reasons
    assert by_name["quiet_one"].category == "ghost"
    assert report.capped is False
    assert counter.calls == 4


async def test_check_capped_sample() -> None:
    big = full_profile("9", "brand", followers=268_000_000, account_type=2)
    followers = [full_profile(str(n), f"fan{n}") for n in range(1, 46)]
    api = _api(followers)
    client, _ = open_client(api_key="hs_test", inner=api.transport())
    async with client:
        report = await check_fake_followers(
            client, target=Profile.from_api(big), sample=100
        )
    assert report.capped is True
    assert len(report.followers) == 45
    assert "newest 45" in report.markdown()


async def test_csv_rows_shape() -> None:
    api = _api([full_profile("1", "real.person")])
    client, _ = open_client(api_key="hs_test", inner=api.transport())
    async with client:
        report = await check_fake_followers(
            client, target=Profile.from_api(TARGET), sample=10
        )
    (row,) = report.csv_rows()
    assert list(row) == [
        "username",
        "profile_url",
        "category",
        "score",
        "reasons",
        "followers",
        "following",
        "posts",
        "is_private",
    ]
    assert row["category"] == "real"
    assert row["profile_url"] == "https://www.instagram.com/real.person/"
