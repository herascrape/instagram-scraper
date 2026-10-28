from decimal import Decimal

import httpx
from herascrape import AsyncClient
from pydantic import BaseModel

PRICE_PER_CALL = Decimal("0.0004")
_FREE_PATHS = ("/system/",)


class Usage(BaseModel):
    """Billed herascrape calls made during one run."""

    calls: int
    cost_usd: Decimal


class CountingTransport(httpx.AsyncBaseTransport):
    """Counts billed calls: every 2xx outside the free `/system/*` paths."""

    def __init__(self, inner: httpx.AsyncBaseTransport | None = None) -> None:
        self._inner = inner or httpx.AsyncHTTPTransport()
        self.calls = 0

    async def handle_async_request(
        self, request: httpx.Request
    ) -> httpx.Response:
        response = await self._inner.handle_async_request(request)
        if response.is_success and not request.url.path.startswith(_FREE_PATHS):
            self.calls += 1
        return response

    async def aclose(self) -> None:
        await self._inner.aclose()

    def usage(self) -> Usage:
        return Usage(calls=self.calls, cost_usd=PRICE_PER_CALL * self.calls)


def open_client(
    *,
    api_key: str | None = None,
    inner: httpx.AsyncBaseTransport | None = None,
) -> tuple[AsyncClient, CountingTransport]:
    """An async herascrape client plus the counter of its billed calls."""
    counter = CountingTransport(inner)
    client = AsyncClient(api_key=api_key, timeout=30.0, transport=counter)
    return client, counter
