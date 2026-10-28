"""Account audit: engagement, cadence, timing, content and audience."""

import re
import statistics
from collections import Counter
from collections.abc import Sequence
from datetime import datetime, timezone
from itertools import pairwise
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from herascrape import AsyncClient
from pydantic import BaseModel, Field

from .client import Usage
from .export import Row
from .fake_followers import CATEGORY_LABELS, FakeFollowerReport
from .fake_followers import check_fake_followers as _check_fake_followers
from .instagram import (
    Account,
    Post,
    PostKind,
    Profile,
    extract_mentions,
    fetch_followers,
    fetch_posts,
)
from .report import cap_note, footer, md_table, percent
from .signals import compact_signals

WEEKDAYS = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]
HOUR_BLOCK = 3
KIND_LABELS: dict[PostKind, str] = {
    "reel": "Reels",
    "carousel": "Carousels",
    "photo": "Photos",
    "video": "Videos",
}
TOP_POSTS = 5
TOP_TAGS = 10
_HASHTAG = re.compile(r"(?<![\w&#])#(\w+)")


class PostMetrics(BaseModel):
    post: Post
    engagement_rate: float | None


class Bucket(BaseModel):
    label: str
    posts: int
    avg_engagement_rate: float | None


class Audience(BaseModel):
    """Signals over the first followers Instagram lists."""

    sample_size: int
    capped: bool
    no_avatar_share: float
    private_share: float
    generated_share: float
    verified_share: float
    deep: FakeFollowerReport | None = None


class AuditReport(BaseModel):
    """The result of `audit_account`."""

    profile: Profile
    tz: str
    posts: list[PostMetrics]
    hidden_likes: int
    er_mean: float | None
    er_median: float | None
    avg_likes: float | None
    avg_comments: float | None
    reels_avg_plays: float | None
    reels_median_plays: float | None
    plays_per_follower: float | None
    posts_per_week: float | None
    median_gap_days: float | None
    longest_gap_days: float | None
    by_weekday: list[Bucket]
    by_hour_block: list[Bucket]
    content_mix: list[Bucket]
    top_posts: list[PostMetrics]
    top_hashtags: list[tuple[str, int]]
    top_mentions: list[tuple[str, int]]
    audience: Audience
    audited_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    usage: Usage | None = None

    def csv_rows(self) -> list[Row]:
        zone = ZoneInfo(self.tz)
        return [
            {
                "url": m.post.url,
                "type": m.post.kind,
                "posted": m.post.taken_at.astimezone(zone).isoformat(),
                "likes": m.post.like_count,
                "comments": m.post.comment_count,
                "plays": m.post.play_count,
                "engagement_rate": m.engagement_rate,
            }
            for m in self.posts
        ]

    def markdown(self) -> str:
        return _markdown(self)


def check_tz(tz: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown time zone {tz!r}") from exc


def _mean(values: Sequence[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _median(values: Sequence[float]) -> float | None:
    return statistics.median(values) if values else None


def _engagement(post: Post, *, followers: int) -> float | None:
    if post.like_count is None or not followers:
        return None
    return (post.like_count + post.comment_count) / followers


def _bucket(label: str, metrics: Sequence[PostMetrics]) -> Bucket:
    rates = [
        m.engagement_rate for m in metrics if m.engagement_rate is not None
    ]
    return Bucket(
        label=label, posts=len(metrics), avg_engagement_rate=_mean(rates)
    )


def _gaps_days(posts: Sequence[Post]) -> list[float]:
    times = sorted(p.taken_at for p in posts)
    return [(b - a).total_seconds() / 86_400 for a, b in pairwise(times)]


def _top(counter: Counter[str]) -> list[tuple[str, int]]:
    return counter.most_common(TOP_TAGS)


async def audit_account(
    client: AsyncClient,
    *,
    profile: Profile,
    posts: int = 24,
    audience_sample: int = 50,
    deep_audience: bool = False,
    tz: str = "UTC",
) -> AuditReport:
    """Audit the newest `posts` grid posts and a follower sample."""
    zone = check_tz(tz)
    grid = await fetch_posts(client, user_pk=profile.pk, limit=posts)
    followers = profile.follower_count
    metrics = [
        PostMetrics(post=p, engagement_rate=_engagement(p, followers=followers))
        for p in grid
    ]
    rates = [
        m.engagement_rate for m in metrics if m.engagement_rate is not None
    ]
    liked = [p for p in grid if p.like_count is not None]
    plays = [
        float(p.play_count) for p in grid if p.kind == "reel" and p.play_count
    ]
    gaps = _gaps_days(grid)
    reels_avg = _mean(plays)
    local = [(m, m.post.taken_at.astimezone(zone)) for m in metrics]
    hashtags: Counter[str] = Counter()
    mentions: Counter[str] = Counter()
    for post in grid:
        hashtags.update({tag.lower() for tag in _HASHTAG.findall(post.caption)})
        mentions.update(
            handle
            for handle in extract_mentions(post.caption)
            if handle != profile.username
        )
    return AuditReport(
        profile=profile,
        tz=tz,
        posts=metrics,
        hidden_likes=len(grid) - len(liked),
        er_mean=_mean(rates),
        er_median=_median(rates),
        avg_likes=_mean([float(p.like_count or 0) for p in liked]),
        avg_comments=_mean([float(p.comment_count) for p in grid]),
        reels_avg_plays=reels_avg,
        reels_median_plays=_median(plays),
        plays_per_follower=(
            reels_avg / followers
            if reels_avg is not None and followers
            else None
        ),
        posts_per_week=(
            len(gaps) / (sum(gaps) / 7) if gaps and sum(gaps) else None
        ),
        median_gap_days=_median(gaps),
        longest_gap_days=max(gaps) if gaps else None,
        by_weekday=[
            _bucket(day, [m for m, t in local if t.weekday() == index])
            for index, day in enumerate(WEEKDAYS)
        ],
        by_hour_block=[
            _bucket(
                f"{start:02d}:00-{start + HOUR_BLOCK:02d}:00",
                [m for m, t in local if start <= t.hour < start + HOUR_BLOCK],
            )
            for start in range(0, 24, HOUR_BLOCK)
        ],
        content_mix=[
            _bucket(label, [m for m in metrics if m.post.kind == kind])
            for kind, label in KIND_LABELS.items()
        ],
        top_posts=sorted(
            (m for m in metrics if m.engagement_rate is not None),
            key=lambda m: m.engagement_rate or 0.0,
            reverse=True,
        )[:TOP_POSTS],
        top_hashtags=_top(hashtags),
        top_mentions=_top(mentions),
        audience=await _audience(
            client, profile=profile, sample=audience_sample, deep=deep_audience
        ),
    )


async def _audience(
    client: AsyncClient, *, profile: Profile, sample: int, deep: bool
) -> Audience:
    report: FakeFollowerReport | None = None
    if deep:
        report = await _check_fake_followers(
            client, target=profile, sample=sample
        )
        accounts: Sequence[Account] = [f.account for f in report.followers]
        capped = report.capped
    else:
        listed = await fetch_followers(client, profile=profile, limit=sample)
        accounts, capped = listed.accounts, listed.capped
    size = len(accounts)

    def share(count: int) -> float:
        return count / size if size else 0.0

    signals = [compact_signals(a) for a in accounts]
    return Audience(
        sample_size=size,
        capped=capped,
        no_avatar_share=share(sum("no_avatar" in s for s in signals)),
        private_share=share(sum(a.is_private for a in accounts)),
        generated_share=share(sum("generated_username" in s for s in signals)),
        verified_share=share(sum(a.is_verified for a in accounts)),
        deep=report,
    )


def _rate(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.2f}%"


def _number(value: float | None, *, digits: int = 0) -> str:
    return "n/a" if value is None else f"{value:,.{digits}f}"


def _days(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f} days"


def _bucket_rows(buckets: Sequence[Bucket]) -> list[list[str]]:
    return [
        [b.label, str(b.posts), _rate(b.avg_engagement_rate)]
        for b in buckets
        if b.posts
    ]


def _markdown(report: AuditReport) -> str:
    profile, tz = report.profile, report.tz
    kind = {1: "personal", 2: "business", 3: "creator"}.get(
        profile.account_type or 0, "unknown type"
    )
    facts = [
        profile.full_name,
        profile.category or "",
        f"{kind} account",
        "verified" if profile.is_verified else "",
        f"{profile.follower_count:,} followers",
        f"{profile.following_count:,} following",
        f"{profile.media_count:,} posts",
    ]
    lines = [
        f"# Instagram audit: @{profile.username}",
        "",
        " · ".join(f for f in facts if f),
        "",
    ]
    if profile.biography:
        lines += [f"> {' '.join(profile.biography.split())}", ""]
    if not report.posts:
        lines += ["No posts to analyze.", ""]
    else:
        times = sorted(m.post.taken_at for m in report.posts)
        lines += [
            f"## Engagement: last {len(report.posts)} posts, "
            f"{times[0]:%Y-%m-%d} to {times[-1]:%Y-%m-%d}",
            "",
        ]
        lines += md_table(
            ["Metric", "Value"],
            [
                ["Engagement rate, mean", _rate(report.er_mean)],
                ["Engagement rate, median", _rate(report.er_median)],
                ["Likes per post", _number(report.avg_likes)],
                ["Comments per post", _number(report.avg_comments, digits=1)],
                ["Reel plays, mean", _number(report.reels_avg_plays)],
                ["Reel plays, median", _number(report.reels_median_plays)],
                [
                    "Reel plays per follower",
                    _number(report.plays_per_follower, digits=2),
                ],
                ["Posts per week", _number(report.posts_per_week, digits=1)],
                ["Median gap between posts", _days(report.median_gap_days)],
                ["Longest gap", _days(report.longest_gap_days)],
            ],
            align="lr",
        )
        lines += [
            "",
            "Engagement rate = (likes + comments) / followers, per post. "
            "Pinned posts are left out.",
        ]
        if report.hidden_likes:
            lines += [
                "",
                f"{report.hidden_likes} posts hide their like counts and are "
                "left out of engagement.",
            ]
        lines += ["", "## Content mix", ""]
        lines += md_table(
            ["Type", "Posts", "Avg engagement"],
            _bucket_rows(report.content_mix),
            align="lrr",
        )
        lines += ["", f"## When posts do best ({tz})", ""]
        lines += md_table(
            ["Day", "Posts", "Avg engagement"],
            _bucket_rows(report.by_weekday),
            align="lrr",
        )
        lines += [""]
        lines += md_table(
            ["Time", "Posts", "Avg engagement"],
            _bucket_rows(report.by_hour_block),
            align="lrr",
        )
        lines += [
            "",
            "Few posts per bucket make these averages noisy; read them with "
            "the post counts.",
        ]
        if report.top_posts:
            lines += ["", "## Top posts", ""]
            lines += md_table(
                ["Post", "Type", "Date", "Likes", "Comments", "Plays", "ER"],
                [
                    [
                        m.post.url,
                        m.post.kind,
                        f"{m.post.taken_at:%Y-%m-%d}",
                        _number(m.post.like_count),
                        f"{m.post.comment_count:,}",
                        _number(m.post.play_count),
                        _rate(m.engagement_rate),
                    ]
                    for m in report.top_posts
                ],
                align="lllrrrr",
            )
    if report.top_hashtags or report.top_mentions:
        lines += ["", "## Hashtags and mentions", ""]
        if report.top_hashtags:
            tags = ", ".join(f"#{t} ({n})" for t, n in report.top_hashtags)
            lines += [f"Hashtags: {tags}", ""]
        if report.top_mentions:
            names = ", ".join(f"@{t} ({n})" for t, n in report.top_mentions)
            lines += [f"Mentions: {names}", ""]
    lines += _audience_markdown(report)
    lines += ["", footer()]
    return "\n".join(lines).replace("\n\n\n", "\n\n") + "\n"


def _audience_markdown(report: AuditReport) -> list[str]:
    audience, profile = report.audience, report.profile
    size = audience.sample_size
    lines = [
        "",
        "## Audience sample",
        "",
        f"A sample of {size:,} of {profile.follower_count:,} followers, as "
        "Instagram lists them.",
    ]
    if audience.capped:
        lines += ["", f"> {cap_note(profile=profile, listed=size)}"]
    lines += [""]
    lines += md_table(
        ["Signal", "Share"],
        [
            ["No profile picture", percent(audience.no_avatar_share, 1)],
            ["Private", percent(audience.private_share, 1)],
            ["Username looks generated", percent(audience.generated_share, 1)],
            ["Verified", percent(audience.verified_share, 1)],
        ],
        align="lr",
    )
    if audience.deep:
        counts = audience.deep.counts
        checked = len(audience.deep.followers)
        lines += [""]
        lines += md_table(
            ["", "Followers", "Share"],
            [
                [label, f"{counts[c]:,}", percent(counts[c], checked)]
                for c, label in CATEGORY_LABELS.items()
            ],
            align="lrr",
        )
    return lines
