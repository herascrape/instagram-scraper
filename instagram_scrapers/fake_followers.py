"""Fake and ghost follower check over a sample of the newest followers."""

import asyncio
from collections import Counter
from datetime import datetime, timezone

from herascrape import AsyncClient, NotFoundError, PrivateAccountError
from pydantic import BaseModel, Field, computed_field

from .client import Usage
from .export import Row
from .instagram import (
    Account,
    OnProgress,
    Profile,
    fetch_followers,
    get_profile_by_pk,
)
from .report import cap_note, footer, md_table, percent
from .signals import (
    SIGNAL_LABELS,
    Category,
    Signal,
    classify,
    profile_signals,
)

CATEGORY_LABELS: dict[Category, str] = {
    "real": "Real",
    "ghost": "Ghost (inactive)",
    "fake": "Fake (bot-like)",
}
FLAGGED_IN_REPORT = 25


class ScoredFollower(BaseModel):
    account: Profile
    category: Category
    score: int
    reasons: list[Signal]


class FakeFollowerReport(BaseModel):
    """The result of `check_fake_followers`."""

    target: Profile
    requested: int
    capped: bool
    followers: list[ScoredFollower]
    unavailable: int = 0
    checked_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    usage: Usage | None = None

    @computed_field
    @property
    def counts(self) -> dict[Category, int]:
        tally = Counter(f.category for f in self.followers)
        return {category: tally[category] for category in CATEGORY_LABELS}

    @computed_field
    @property
    def signal_counts(self) -> dict[Signal, int]:
        tally = Counter(s for f in self.followers for s in f.reasons)
        return {signal: tally[signal] for signal in SIGNAL_LABELS}

    def csv_rows(self) -> list[Row]:
        return [
            {
                "username": f.account.username,
                "profile_url": f.account.url,
                "category": f.category,
                "score": f.score,
                "reasons": ";".join(f.reasons),
                "followers": f.account.follower_count,
                "following": f.account.following_count,
                "posts": f.account.media_count,
                "is_private": f.account.is_private,
            }
            for f in self.followers
        ]

    def markdown(self) -> str:
        return _markdown(self)


async def check_fake_followers(
    client: AsyncClient,
    *,
    target: Profile,
    sample: int = 100,
    concurrency: int = 8,
    on_progress: OnProgress | None = None,
) -> FakeFollowerReport:
    """Score the newest `sample` followers of `target`."""
    listed = await fetch_followers(client, profile=target, limit=sample)
    gate = asyncio.Semaphore(concurrency)
    done = 0

    async def score(account: Account) -> ScoredFollower | None:
        nonlocal done
        async with gate:
            try:
                profile = await get_profile_by_pk(client, pk=account.pk)
            except (NotFoundError, PrivateAccountError):
                return None
            finally:
                done += 1
                if on_progress:
                    on_progress(done)
        if profile.has_anonymous_profile_picture is None:
            profile.has_anonymous_profile_picture = (
                account.has_anonymous_profile_picture
            )
        reasons = profile_signals(profile)
        category, total = classify(
            signals=reasons, verified=profile.is_verified
        )
        return ScoredFollower(
            account=profile, category=category, score=total, reasons=reasons
        )

    results = await asyncio.gather(*(score(a) for a in listed.accounts))
    scored = [result for result in results if result is not None]
    return FakeFollowerReport(
        target=target,
        requested=sample,
        capped=listed.capped,
        followers=scored,
        unavailable=len(results) - len(scored),
    )


def _markdown(report: FakeFollowerReport) -> str:
    target = report.target
    checked = len(report.followers)
    lines = [
        f"# Fake follower check: @{target.username}",
        "",
        f"Sample: the newest {checked:,} of {target.follower_count:,} "
        f"followers, checked {report.checked_at:%Y-%m-%d}.",
        "",
    ]
    if report.capped:
        lines += [f"> {cap_note(profile=target, listed=checked)}", ""]
    lines += md_table(
        ["", "Followers", "Share"],
        [
            [label, f"{report.counts[c]:,}", percent(report.counts[c], checked)]
            for c, label in CATEGORY_LABELS.items()
        ],
        align="lrr",
    )
    counts = report.signal_counts
    seen: list[tuple[Signal, int]] = [
        (s, counts[s]) for s in SIGNAL_LABELS if counts[s]
    ]
    if seen:
        lines += ["", "## Signals", ""]
        lines += md_table(
            ["Signal", "Accounts"],
            [[SIGNAL_LABELS[s], f"{n:,}"] for s, n in seen],
            align="lr",
        )
    flagged = sorted(
        (f for f in report.followers if f.category != "real"),
        key=lambda f: (f.category != "fake", -f.score),
    )
    if flagged:
        lines += ["", "## Flagged accounts", ""]
        lines += md_table(
            ["Account", "Category", "Followers", "Following", "Posts", "Why"],
            [
                [
                    f"@{f.account.username}",
                    f.category,
                    f"{f.account.follower_count:,}",
                    f"{f.account.following_count:,}",
                    f"{f.account.media_count:,}",
                    ", ".join(SIGNAL_LABELS[s] for s in f.reasons),
                ]
                for f in flagged[:FLAGGED_IN_REPORT]
            ],
            align="llrrrl",
        )
        if len(flagged) > FLAGGED_IN_REPORT:
            lines += [
                "",
                f"{len(flagged) - FLAGGED_IN_REPORT} more in the CSV export.",
            ]
    lines += [
        "",
        "This is an estimate from a sample, not a verdict on any account. "
        "Instagram lists followers newest first, so a recent wave of bot "
        "follows weighs more here than in the whole audience.",
    ]
    if report.unavailable:
        lines += [
            "",
            f"{report.unavailable} sampled accounts could not be read "
            "(deleted or restricted) and are left out.",
        ]
    lines += ["", footer()]
    return "\n".join(lines) + "\n"
