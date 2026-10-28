from decimal import Decimal

import pytest
from herascrape import NotFoundError

from instagram_scrapers.client import open_client
from tests.factories import full_profile
from tests.fakeapi import FakeAPI


async def test_counts_only_billed_calls() -> None:
    api = FakeAPI()
    api.route("/users/1", full_profile("1"))
    api.route("/system/balance", {"balance_usd": "1.00", "currency": "USD"})
    client, counter = open_client(api_key="hs_test", inner=api.transport())
    async with client:
        await client.user("1")
        await client.balance()
        with pytest.raises(NotFoundError):
            await client.user("2")
    assert counter.calls == 1
    assert counter.usage().cost_usd == Decimal("0.0004")
