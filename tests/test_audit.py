from datetime import datetime, timezone

import pytest

from instagram_scrapers.audit import AuditReport, audit_account
from instagram_scrapers.client import open_client
from instagram_scrapers.instagram import JSON, Profile
from tests.factories import (
    compact_user,
    full_profile,
    grid_page,
    list_page,
    post_detail,
)
from tests.fakeapi import FakeAPI

DAY = 86_400
# 2026-10-26 22:30 UTC, a Monday; 00:30 on Tuesday in Kyiv (UTC+2).
MONDAY_LATE = 1_793_053_800


def _api(*, followers: int, posts: list[JSON]) -> tuple[FakeAPI, Profile]:
    api = FakeAPI()
    api.route("/users/9/posts", grid_page(posts, cursor=None))
    audience = [
        compact_user("1", "fan.one"),
        compact_user("2", "user84629173", full_name="", anonymous_pic=True),
        compact_user("3", "locked", is_private=True),
        compact_user("4", "star", is_verified=True),
    ]
    api.route("/users/9/followers", list_page(audience, next_cursor=None))
    profile = Profile.from_api(full_profile("9", "bakery", followers=followers))
    return api, profile


async def _audit(
    api: FakeAPI, profile: Profile, *, tz: str = "UTC", posts: int = 24
) -> AuditReport:
    client, _ = open_client(api_key="hs_test", inner=api.transport())
    async with client:
        return await audit_account(
            client, profile=profile, posts=posts, audience_sample=4, tz=tz
        )


async def test_engagement_rate() -> None:
    api, profile = _api(
        followers=1000,
        posts=[
            post_detail("1", likes=90, comments=10),
            post_detail("2", likes=20, comments=0),
            post_detail("3", likes=30, comments=0),
        ],
    )
    report = await _audit(api, profile)
    assert [p.engagement_rate for p in report.posts] == [0.1, 0.02, 0.03]
    assert report.er_mean == pytest.approx(0.05)
    assert report.er_median == pytest.approx(0.03)
    assert report.top_posts[0].post.pk == "1"


async def test_hidden_likes_excluded() -> None:
    api, profile = _api(
        followers=100,
        posts=[
            post_detail("1", likes=None, comments=5),
            post_detail("2", likes=10, comments=0),
        ],
    )
    report = await _audit(api, profile)
    assert report.hidden_likes == 1
    assert report.posts[0].engagement_rate is None
    assert report.er_mean == pytest.approx(0.1)
    assert "hide their like counts" in report.markdown()


async def test_zero_followers() -> None:
    api, profile = _api(followers=0, posts=[post_detail("1")])
    report = await _audit(api, profile)
    assert report.er_mean is None
    assert report.plays_per_follower is None
    assert "n/a" in report.markdown()


async def test_no_posts() -> None:
    api, profile = _api(followers=10, posts=[])
    report = await _audit(api, profile)
    assert report.posts == []
    assert report.posts_per_week is None
    assert "No posts" in report.markdown()


async def test_cadence() -> None:
    base = 1_790_000_000
    api, profile = _api(
        followers=100,
        posts=[
            post_detail("3", taken_at=base + 14 * DAY),
            post_detail("2", taken_at=base + 4 * DAY),
            post_detail("1", taken_at=base),
        ],
    )
    report = await _audit(api, profile)
    assert report.posts_per_week == pytest.approx(1.0)
    assert report.median_gap_days == pytest.approx(7.0)
    assert report.longest_gap_days == pytest.approx(10.0)


async def test_timing_buckets_in_tz() -> None:
    api, profile = _api(
        followers=100, posts=[post_detail("1", taken_at=MONDAY_LATE)]
    )
    utc = await _audit(api, profile)
    kyiv = await _audit(api, profile, tz="Europe/Kyiv")
    assert [(b.label, b.posts) for b in utc.by_weekday if b.posts] == [
        ("Monday", 1)
    ]
    assert [b.label for b in kyiv.by_weekday if b.posts] == ["Tuesday"]
    assert [b.label for b in kyiv.by_hour_block if b.posts] == ["00:00-03:00"]
    assert len(kyiv.by_weekday) == 7
    assert len(kyiv.by_hour_block) == 8


async def test_content_mix_and_reels() -> None:
    api, profile = _api(
        followers=1000,
        posts=[
            post_detail("1", product_type="clips", media_type=2, plays=3000),
            post_detail("2", product_type="clips", media_type=2, plays=1000),
            post_detail("3", product_type="carousel_container", media_type=8),
            post_detail("4"),
        ],
    )
    report = await _audit(api, profile)
    mix = {b.label: b.posts for b in report.content_mix}
    assert mix == {"Reels": 2, "Carousels": 1, "Photos": 1, "Videos": 0}
    assert report.reels_avg_plays == 2000
    assert report.reels_median_plays == 2000
    assert report.plays_per_follower == pytest.approx(2.0)


async def test_top_hashtags_and_mentions() -> None:
    api, profile = _api(
        followers=100,
        posts=[
            post_detail("1", caption="Rye #Sourdough with @Mill #bread"),
            post_detail("2", caption="#sourdough again, thanks @mill @bakery"),
            post_detail("3", caption="email me a@b.com #1"),
        ],
    )
    report = await _audit(api, profile)
    assert report.top_hashtags[:2] == [("sourdough", 2), ("bread", 1)]
    assert report.top_mentions == [("mill", 2)]


async def test_audience_light() -> None:
    api, profile = _api(followers=4, posts=[])
    report = await _audit(api, profile)
    audience = report.audience
    assert audience.sample_size == 4
    assert audience.capped is False
    assert audience.no_avatar_share == pytest.approx(0.25)
    assert audience.private_share == pytest.approx(0.25)
    assert audience.generated_share == pytest.approx(0.25)
    assert audience.verified_share == pytest.approx(0.25)
    assert audience.deep is None


async def test_bad_tz_rejected() -> None:
    api, profile = _api(followers=10, posts=[])
    with pytest.raises(ValueError, match="Mars/Base"):
        await _audit(api, profile, tz="Mars/Base")


async def test_csv_rows_and_markdown() -> None:
    api, profile = _api(followers=100, posts=[post_detail("1", likes=5)])
    report = await _audit(api, profile)
    (row,) = report.csv_rows()
    assert row["url"] == "https://www.instagram.com/p/C1/"
    assert row["engagement_rate"] == pytest.approx(0.07)
    text = report.markdown()
    assert text.startswith("# Instagram audit: @bakery")
    assert "## Content mix" in text
    assert report.audited_at.tzinfo == timezone.utc
    assert isinstance(report.audited_at, datetime)
