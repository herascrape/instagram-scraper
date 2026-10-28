import json

import httpx
import pytest
from herascrape import AsyncClient
from typer.testing import CliRunner

from instagram_scrapers import __version__, cli
from instagram_scrapers.cli import app
from instagram_scrapers.client import CountingTransport, open_client
from tests.factories import (
    comment,
    comments_page,
    compact_user,
    full_profile,
    list_page,
    post_body,
)
from tests.fakeapi import FakeAPI

runner = CliRunner()


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> FakeAPI:
    """Route every CLI command to a FakeAPI with a dummy key."""
    fake = FakeAPI()

    def fake_open_client(
        *,
        api_key: str | None = None,
        inner: httpx.AsyncBaseTransport | None = None,
    ) -> tuple[AsyncClient, CountingTransport]:
        return open_client(api_key=api_key, inner=fake.transport())

    monkeypatch.setattr(cli, "open_client", fake_open_client)
    monkeypatch.setenv("HERASCRAPE_API_KEY", "hs_test")
    return fake


def test_version_flag() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == f"instagram-scraper {__version__}"


def test_missing_key_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HERASCRAPE_API_KEY", raising=False)
    result = runner.invoke(app, ["fake-followers", "bakery"])
    assert result.exit_code == 2
    assert "HERASCRAPE_API_KEY" in result.stderr
    assert "herascrape.com" in result.stderr


def test_bad_username_is_a_usage_error(api: FakeAPI) -> None:
    result = runner.invoke(app, ["fake-followers", "not a name"])
    assert result.exit_code == 2
    assert api.requests == []


def test_unknown_output_suffix_fails_before_any_call(api: FakeAPI) -> None:
    result = runner.invoke(app, ["fake-followers", "bakery", "-o", "x.xlsx"])
    assert result.exit_code == 2
    assert ".csv, .json, .md" in result.stderr
    assert api.requests == []


def test_cli_private_account_message(api: FakeAPI) -> None:
    api.route(
        "/users/username/bakery",
        full_profile("9", "bakery", is_private=True),
    )
    result = runner.invoke(app, ["fake-followers", "bakery"])
    assert result.exit_code == 1
    assert "never logs in" in result.stderr
    assert "Traceback" not in result.output
    assert api.paths("/users/9") == []


def test_profile_not_found_message(api: FakeAPI) -> None:
    api.error(
        "/users/username/nobody",
        status=404,
        error_code="profile_not_found",
    )
    result = runner.invoke(app, ["fake-followers", "@nobody"])
    assert result.exit_code == 1
    assert "@nobody" in result.stderr


def test_insufficient_balance_message(api: FakeAPI) -> None:
    api.error(
        "/users/username/bakery",
        status=402,
        error_code="insufficient_balance",
    )
    result = runner.invoke(app, ["fake-followers", "bakery"])
    assert result.exit_code == 1
    assert "herascrape.com/payments" in result.stderr


def test_fake_followers_json_flag(api: FakeAPI) -> None:
    api.route("/users/username/bakery", full_profile("9", "bakery"))
    api.route(
        "/users/9/followers",
        list_page([compact_user("1", "fan")], next_cursor=None),
    )
    api.route("/users/1", full_profile("1", "fan"))
    result = runner.invoke(app, ["fake-followers", "bakery", "--json"])
    assert result.exit_code == 0, result.output
    body = json.loads(result.stdout)
    assert body["counts"] == {"real": 1, "ghost": 0, "fake": 0}
    assert body["usage"]["calls"] == 3
    assert "3 API calls" in result.stderr


def _giveaway_api(api: FakeAPI, *, comment_count: int = 3) -> None:
    owner = compact_user("9", "bakery")
    api.route(
        "/posts/DQSPu0hE_pP",
        post_body(
            "77", code="DQSPu0hE_pP", owner=owner, comment_count=comment_count
        ),
    )
    fans = [
        comment(str(n), user=compact_user(str(n), f"fan{n}"), text="@a me")
        for n in range(1, 4)
    ]
    api.route("/posts/77/comments", comments_page(fans, next_min_id=None))


def test_giveaway_json(api: FakeAPI) -> None:
    _giveaway_api(api)
    args = [
        "giveaway",
        "https://www.instagram.com/p/DQSPu0hE_pP/?igsh=x",
        "--seed",
        "s",
        "--min-mentions",
        "1",
        "--until",
        "2030-01-01T00:00Z",
        "--json",
    ]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    body = json.loads(result.stdout)
    assert body["seed"] == "s"
    assert len(body["winners"]) == 1
    assert body["rules"]["until"] == "2030-01-01T00:00:00Z"


def test_giveaway_bad_until(api: FakeAPI) -> None:
    result = runner.invoke(app, ["giveaway", "ABCDEF", "--until", "soon"])
    assert result.exit_code == 2
    assert api.requests == []


def test_budget_guard_non_interactive(api: FakeAPI) -> None:
    _giveaway_api(api, comment_count=60_000)
    result = runner.invoke(app, ["giveaway", "DQSPu0hE_pP"])
    assert result.exit_code == 2
    assert "4,001 API calls" in result.stderr
    assert "--yes" in result.stderr
    assert api.paths("/posts/77/comments") == []
