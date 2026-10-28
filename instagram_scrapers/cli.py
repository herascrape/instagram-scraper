import asyncio
import sqlite3
import sys
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
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
from pydantic import ValidationError
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
from .audit import AuditReport, audit_account, check_tz
from .client import PRICE_PER_CALL, Usage, open_client
from .export import Exportable, check_outputs, write_outputs
from .fake_followers import FakeFollowerReport, check_fake_followers
from .followers import (
    FollowerDiff,
    SnapshotStore,
    default_db_path,
    estimate_follower_calls,
    track_followers,
)
from .giveaway import (
    GiveawayResult,
    GiveawayRules,
    UnknownAccountError,
    estimate_giveaway_calls,
    run_giveaway,
)
from .instagram import (
    PrivateProfileError,
    get_post,
    get_public_profile,
    parse_post_ref,
    parse_username,
)

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


def _post_ref(value: str) -> str:
    try:
        return parse_post_ref(value)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _moment(value: str | None) -> datetime | None:
    """ISO 8601 time; UTC unless it carries an offset."""
    if value is None:
        return None
    try:
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise typer.BadParameter(
            f"{value!r} is not an ISO time like 2026-11-01T18:00"
        ) from exc
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


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
    if isinstance(exc, UnknownAccountError):
        return (
            f"@{exc.username}: no public Instagram profile by that name "
            "(from --must-follow)."
        )
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
    except (HerascrapeError, PrivateProfileError, UnknownAccountError) as exc:
        _print_usage(counter.usage())
        _fail(_error_message(exc, target=target), code=1)
    except (OSError, sqlite3.Error, ValueError) as exc:
        _print_usage(counter.usage())
        _fail(_local_error_message(exc), code=1)
    result.usage = counter.usage()
    if as_json:
        typer.echo(result.model_dump_json(indent=2))
    else:
        out.print(Markdown(result.markdown()))
    try:
        write_outputs(result, paths=paths)
    except OSError as exc:
        _print_usage(result.usage)
        _fail(_local_error_message(exc), code=1)
    for path in paths:
        err.print(f"Wrote {path}")
    _print_usage(result.usage)


def _local_error_message(exc: Exception) -> str:
    if isinstance(exc, sqlite3.Error):
        return f"Can't use the snapshot database: {exc}"
    if isinstance(exc, ValidationError):
        return (
            "Instagram returned data in a shape this version can't read. "
            "Please open an issue at "
            "https://github.com/herascrape/instagram-scraper/issues with the "
            "command you ran."
        )
    if isinstance(exc, OSError):
        where = f": {exc.filename}" if exc.filename else ""
        return f"{exc.strerror or exc}{where}"
    return str(exc)


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
            help="How many followers to check.",
        ),
    ] = 100,
    api_key: ApiKey = None,
    output: Outputs = None,
    as_json: AsJson = False,
) -> None:
    """Estimate fake and ghost followers from a sample of followers.

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


@app.command()
def giveaway(
    post: Annotated[
        str,
        typer.Argument(
            help="Post or reel URL, shortcode or media id.",
            show_default=False,
        ),
    ],
    winners: Annotated[
        int, typer.Option("--winners", "-w", min=1, help="Winners to draw.")
    ] = 1,
    backups: Annotated[
        int, typer.Option(min=0, help="Alternates drawn after the winners.")
    ] = 2,
    min_mentions: Annotated[
        int,
        typer.Option(min=0, help="Other accounts a comment must @tag."),
    ] = 0,
    keyword: Annotated[
        list[str] | None,
        typer.Option(
            help="Text a comment must contain. Repeat to require several.",
            show_default=False,
        ),
    ] = None,
    until: Annotated[
        str | None,
        typer.Option(
            help="Close time (ISO 8601, UTC unless an offset is given). "
            "Later comments don't count.",
            show_default=False,
        ),
    ] = None,
    max_entries_per_user: Annotated[
        int, typer.Option(min=1, help="Comments that count per account.")
    ] = 1,
    exclude: Annotated[
        list[str] | None,
        typer.Option(
            help="Account that can't win. Repeatable.", show_default=False
        ),
    ] = None,
    min_followers: Annotated[
        int,
        typer.Option(min=0, help="Winner needs this many followers."),
    ] = 0,
    min_posts: Annotated[
        int, typer.Option(min=0, help="Winner needs this many posts.")
    ] = 0,
    require_avatar: Annotated[
        bool, typer.Option(help="Winner needs a profile picture.")
    ] = False,
    must_follow: Annotated[
        list[str] | None,
        typer.Option(
            help="Account the winner must follow, e.g. the host. Repeatable.",
            show_default=False,
        ),
    ] = None,
    follow_scan_limit: Annotated[
        int,
        typer.Option(
            min=25,
            help="For --must-follow, skip drawn accounts that follow more "
            "accounts than this (checking costs 1 call per 25, read up to 5 "
            "times).",
        ),
    ] = 2000,
    max_check_calls: Annotated[
        int,
        typer.Option(
            min=1,
            help="Stop checking drawn accounts after this many calls "
            "(winner checks only; the comment fetch is separate).",
        ),
    ] = 1000,
    seed: Annotated[
        str | None,
        typer.Option(
            help="Draw seed. Publish it to let anyone re-run the draw; "
            "random if omitted.",
            show_default=False,
        ),
    ] = None,
    yes: Yes = False,
    api_key: ApiKey = None,
    output: Outputs = None,
    as_json: AsJson = False,
) -> None:
    """Pick giveaway winners from a post's comments, provably fairly.

    Fetching comments costs one call per 15 (1,000 comments: about 68 calls,
    $0.03). Winner checks (--min-followers, --must-follow, ...) run only on
    drawn accounts.
    """
    ref = _post_ref(post)
    rules = GiveawayRules(
        winners=winners,
        backups=backups,
        min_mentions=min_mentions,
        keywords=keyword or [],
        until=_moment(until),
        max_entries_per_user=max_entries_per_user,
        exclude=[_username(name) for name in exclude or []],
        min_followers=min_followers,
        min_posts=min_posts,
        require_avatar=require_avatar,
        must_follow=[_username(name) for name in must_follow or []],
        max_following_to_scan=follow_scan_limit,
        max_check_calls=max_check_calls,
    )

    async def job(client: AsyncClient, session: Session) -> GiveawayResult:
        info = await get_post(client, ref=ref)
        session.confirm_budget(
            estimate=estimate_giveaway_calls(post=info, rules=rules), yes=yes
        )
        return await run_giveaway(
            client,
            post=info,
            rules=rules,
            seed=seed,
            on_progress=session.advance,
        )

    _run(
        job,
        target=ref,
        label="Fetching comments",
        api_key=api_key,
        outputs=output,
        as_json=as_json,
    )


@app.command()
def audit(
    username: Annotated[
        str,
        typer.Argument(
            help="Account to audit: name, @name or profile URL.",
            show_default=False,
        ),
    ],
    posts: Annotated[
        int,
        typer.Option(min=1, max=240, help="Newest grid posts to analyze."),
    ] = 24,
    audience_sample: Annotated[
        int,
        typer.Option(min=1, max=1000, help="Followers to sample."),
    ] = 50,
    deep_audience: Annotated[
        bool,
        typer.Option(
            help="Score each sampled follower as real, ghost or fake "
            "(one profile call each).",
        ),
    ] = False,
    tz: Annotated[
        str,
        typer.Option(help="Time zone for the best day and time tables."),
    ] = "UTC",
    api_key: ApiKey = None,
    output: Outputs = None,
    as_json: AsJson = False,
) -> None:
    """Audit an account: engagement, cadence, timing, content and audience.

    Defaults cost about 6 calls ($0.003): 3 pages of posts, 2 of followers.
    """
    name = _username(username)
    try:
        check_tz(tz)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--tz") from exc

    async def job(client: AsyncClient, session: Session) -> AuditReport:
        target = await get_public_profile(client, username=name)
        return await audit_account(
            client,
            profile=target,
            posts=posts,
            audience_sample=audience_sample,
            deep_audience=deep_audience,
            tz=tz,
        )

    _run(
        job,
        target=f"@{name}",
        label="Auditing",
        api_key=api_key,
        outputs=output,
        as_json=as_json,
    )


@app.command()
def followers(
    username: Annotated[
        str,
        typer.Argument(
            help="Public account to track: name, @name or profile URL.",
            show_default=False,
        ),
    ],
    db: Annotated[
        Path | None,
        typer.Option(
            help="Snapshot database (default: your user data folder).",
            show_default=False,
        ),
    ] = None,
    yes: Yes = False,
    api_key: ApiKey = None,
    output: Outputs = None,
    as_json: AsJson = False,
) -> None:
    """Who followed and unfollowed since the last run.

    Saves a snapshot of the full follower list and compares it with the
    previous one. The list is read up to 8 times at 1 call per 25 followers
    (up to 5 reads find everyone, 3 more confirm unfollowers): 10,000
    followers cost up to 3,200 calls ($1.28).
    """
    name = _username(username)
    path = db or default_db_path()

    async def job(client: AsyncClient, session: Session) -> FollowerDiff:
        target = await get_public_profile(client, username=name)
        session.confirm_budget(
            estimate=estimate_follower_calls(target), yes=yes
        )
        with SnapshotStore(path=path) as store:
            return await track_followers(
                client, profile=target, store=store, on_progress=session.advance
            )

    _run(
        job,
        target=f"@{name}",
        label="Listing followers",
        api_key=api_key,
        outputs=output,
        as_json=as_json,
    )


def main() -> None:
    app()
