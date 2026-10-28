"""Small real runs against api.herascrape.com. About 40 billed calls."""

from pathlib import Path

import pytest

from instagram_scrapers.audit import audit_account
from instagram_scrapers.client import open_client
from instagram_scrapers.fake_followers import check_fake_followers
from instagram_scrapers.followers import SnapshotStore, track_followers
from instagram_scrapers.giveaway import GiveawayRules, run_giveaway
from instagram_scrapers.instagram import get_post, get_public_profile

pytestmark = pytest.mark.live

BRAND = "natgeo"
SMALL_ACCOUNT = "micro.bakeryy"
SMALL_POST = "DevLWnLlYb6"


async def test_fake_followers() -> None:
    client, counter = open_client()
    async with client:
        target = await get_public_profile(client, username=BRAND)
        report = await check_fake_followers(client, target=target, sample=10)
    assert len(report.followers) + report.unavailable == 10
    assert sum(report.counts.values()) == len(report.followers)
    assert counter.calls >= 11


async def test_audit() -> None:
    client, _ = open_client()
    async with client:
        target = await get_public_profile(client, username=BRAND)
        report = await audit_account(
            client, profile=target, posts=9, audience_sample=10
        )
    assert len(report.posts) == 9
    assert report.er_mean is not None
    assert report.audience.sample_size > 0


async def test_giveaway() -> None:
    client, _ = open_client()
    async with client:
        post = await get_post(client, ref=SMALL_POST)
        result = await run_giveaway(
            client, post=post, rules=GiveawayRules(), seed="live"
        )
    assert result.comments_fetched > 0
    assert result.winners
    assert result.winners[0].ticket.rank == 1


async def test_followers(tmp_path: Path) -> None:
    client, _ = open_client()
    async with client:
        target = await get_public_profile(client, username=SMALL_ACCOUNT)
        with SnapshotStore(path=tmp_path / "f.sqlite3") as store:
            diff = await track_followers(client, profile=target, store=store)
    assert diff.previous is None
    assert diff.current.fetched > 0
