import asyncio
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated, NoReturn, Protocol, TypeVar

import typer
from herascrape import (
    AsyncClient,
    AuthenticationError,
    HerascrapeAPIError,
    HerascrapeError,
    NotFoundError,
    PrivateAccountError,
    ProfileNotFoundError,
    RateLimitError,
)
from rich.console import Console
from rich.markdown import Markdown
from rich.progress import (
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)

from . import __version__
from .client import PRICE_PER_CALL, Usage, open_client
from .export import Exportable, check_outputs, write_outputs
from .fake_followers import FakeFollowerReport, check_fake_followers
from .instagram import PrivateProfileError, get_public_profile, parse_username

BUDGET_CONFIRM_CALLS = 1250

NO_KEY = (
    "No API key. Set HERASCRAPE_API_KEY or pass --api-key.\n"
    "Create a key at https://herascrape.com/api-keys. New accounts get $1 of "
    "free balance (about 2,500 calls) once Telegram is connected."
)

app = typer.Typer(
    name="instagram-scraper",
    help=(
        "Instagram fake follower checker, giveaway picker, account audit and "
        "follower tracker. Runs on the herascrape API: no Instagram login."
    ),
    no_args_is_help=True,
    rich_markup_mode="rich",
)
err = Console(stderr=True, highlight=False)
out = Console(highlight=False)

ApiKey = Annotated[
    str | None,
    typer.Option(
        "--api-key",
        envvar="HERASCRAPE_API_KEY",
        show_envvar=True,
        help="herascrape API key.",
        show_default=False,
    ),
]
Outputs = Annotated[
    list[Path] | None,
    typer.Option(
        "--output",
        "-o",
        help="Also write the result to FILE (.csv, .json or .md). Repeatable.",
        metavar="FILE",
        show_default=False,
    ),
]
AsJson = Annotated[
    bool,
    typer.Option("--json", help="Print the result as JSON, not the report."),
]
Yes = Annotated[
    bool,
    typer.Option("--yes", "-y", help="Skip the cost confirmation."),
]


class Result(Exportable, Protocol):
    usage: Usage | None


R = TypeVar("R", bound=Result)


class Session:
    """What a running command may do to the terminal."""

    def __init__(self, *, progress: Progress, task: TaskID, label: str) -> None:
        self._progress = progress
        self._task = task
        self._label = label

    def advance(self, done: int) -> None:
        self._progress.update(
            self._task, description=f"{self._label}: {done:,}"
        )

    def confirm_budget(self, *, estimate: int, yes: bool) -> None:
        """Ask before a run larger than `BUDGET_CONFIRM_CALLS` calls."""
        if yes or estimate <= BUDGET_CONFIRM_CALLS:
            return
        cost = PRICE_PER_CALL * estimate
        question = f"This run needs about {estimate:,} API calls (${cost:.2f})."
        if not sys.stdin.isatty():
            _fail(f"{question} Pass --yes to run it.", code=2)
        self._progress.stop()
        if not typer.confirm(f"{question} Continue?", err=True):
            raise typer.Exit(2)
        self._progress.start()


Job = Callable[[AsyncClient, Session], Awaitable[R]]


def _fail(message: str, *, code: int) -> NoReturn:
    typer.echo(message, err=True)
    raise typer.Exit(code)


def _username(value: str) -> str:
    try:
        return parse_username(value)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


async def _execute(job: Job[R], *, client: AsyncClient, label: str) -> R:
    async with client:
        with Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            TimeElapsedColumn(),
            console=err,
            transient=True,
        ) as progress:
            task = progress.add_task(label)
            session = Session(progress=progress, task=task, label=label)
            return await job(client, session)


def _error_message(exc: Exception, *, target: str) -> str:
    if isinstance(exc, (PrivateProfileError, PrivateAccountError)):
        return (
            f"{target} is private. Private accounts can't be read without "
            "logging in, and this tool never logs in."
        )
    if isinstance(exc, ProfileNotFoundError):
        return f"{target}: no public Instagram profile by that name."
    if isinstance(exc, NotFoundError):
        return f"{target}: not found on Instagram."
    if isinstance(exc, RateLimitError):
        wait = f" Wait {exc.retry_after:.0f}s and" if exc.retry_after else ""
        return f"Rate limited by the herascrape API.{wait} Run it again."
    if isinstance(exc, AuthenticationError):
        return (
            "The API key was rejected. Check it at "
            "https://herascrape.com/api-keys."
        )
    if (
        isinstance(exc, HerascrapeAPIError)
        and exc.error_code == "insufficient_balance"
    ):
        return (
            "Your herascrape balance is too low for this run. Top up at "
            "https://herascrape.com/payments."
        )
    return f"herascrape API error: {exc}"


def _run(
    job: Job[R],
    *,
    target: str,
    label: str,
    api_key: str | None,
    outputs: list[Path] | None,
    as_json: bool,
) -> None:
    paths = outputs or []
    try:
        check_outputs(paths)
    except ValueError as exc:
        _fail(str(exc), code=2)
    try:
        client, counter = open_client(api_key=api_key)
    except AuthenticationError:
        _fail(NO_KEY, code=2)
    try:
        result = asyncio.run(_execute(job, client=client, label=label))
    except (HerascrapeError, PrivateProfileError) as exc:
        _print_usage(counter.usage())
        _fail(_error_message(exc, target=target), code=1)
    result.usage = counter.usage()
    write_outputs(result, paths=paths)
    if as_json:
        typer.echo(result.model_dump_json(indent=2))
    else:
        out.print(Markdown(result.markdown()))
    for path in paths:
        err.print(f"Wrote {path}")
    _print_usage(result.usage)


def _print_usage(usage: Usage) -> None:
    err.print(f"{usage.calls:,} API calls · ${usage.cost_usd:.4f}")


def _print_version(value: bool) -> None:
    if value:
        typer.echo(f"instagram-scraper {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_print_version,
            is_eager=True,
            help="Show the version and exit.",
        ),
    ] = False,
) -> None:
    """Instagram scraper CLI."""


@app.command("fake-followers")
def fake_followers(
    username: Annotated[
        str,
        typer.Argument(
            help="Account to check: name, @name or profile URL.",
            show_default=False,
        ),
    ],
    sample: Annotated[
        int,
        typer.Option(
            "--sample",
            "-n",
            min=1,
            max=1000,
            help="How many of the newest followers to check.",
        ),
    ] = 100,
    api_key: ApiKey = None,
    output: Outputs = None,
    as_json: AsJson = False,
) -> None:
    """Estimate fake and ghost followers from the newest followers.

    One profile call per sampled follower: --sample 100 costs about 105
    calls ($0.04).
    """
    name = _username(username)

    async def job(client: AsyncClient, session: Session) -> FakeFollowerReport:
        target = await get_public_profile(client, username=name)
        return await check_fake_followers(
            client, target=target, sample=sample, on_progress=session.advance
        )

    _run(
        job,
        target=f"@{name}",
        label="Checking followers",
        api_key=api_key,
        outputs=output,
        as_json=as_json,
    )


def main() -> None:
    app()
