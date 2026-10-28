import httpx

from instagram_scrapers.instagram import JSON

_Key = tuple[str, frozenset[tuple[str, str]]]


class FakeAPI:
    """A herascrape API stand-in for `httpx.MockTransport`.

    A route answers a path with an exact query; anything unrouted gets a
    404 naming the request, so a missing fixture fails loudly.
    """

    def __init__(self) -> None:
        self._routes: dict[_Key, tuple[int, JSON]] = {}
        self.requests: list[httpx.Request] = []

    def route(
        self,
        path: str,
        body: JSON,
        *,
        query: dict[str, str] | None = None,
        status: int = 200,
    ) -> None:
        key = (path, frozenset((query or {}).items()))
        self._routes[key] = (status, body)

    def error(
        self,
        path: str,
        *,
        status: int,
        error_code: str,
        query: dict[str, str] | None = None,
    ) -> None:
        body: JSON = {
            "status": "client_error",
            "error_code": error_code,
            "detail": error_code,
        }
        self.route(path, body, query=query, status=status)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = (request.url.path, frozenset(request.url.params.items()))
        if key not in self._routes:
            return httpx.Response(
                404,
                json={
                    "status": "client_error",
                    "error_code": "not_found",
                    "detail": f"unrouted {request.url}",
                },
            )
        status, body = self._routes[key]
        return httpx.Response(status, json=body)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def paths(self, prefix: str = "/") -> list[str]:
        return [
            r.url.path for r in self.requests if r.url.path.startswith(prefix)
        ]
