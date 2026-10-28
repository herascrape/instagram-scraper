"""Provably fair giveaway draw over a post's comments."""

import hashlib
import secrets
from collections import Counter
from collections.abc import Sequence
from datetime import datetime, timezone

from herascrape import (
    AsyncClient,
    NotFoundError,
    PrivateAccountError,
    ProfileNotFoundError,
)
from pydantic import BaseModel, Field, computed_field, field_validator

from .client import Usage
from .export import Row
from .instagram import (
    COMMENTS_PAGE_SIZE,
    Account,
    Comment,
    OnProgress,
    PostInfo,
    estimate_calls,
    extract_mentions,
    fetch_comments,
    get_profile,
    get_profile_by_pk,
    walk_until_complete,
)
from .report import footer, md_table, plural

METHOD = (
    "rank = sha256(seed + ':' + comment_pk), ascending; entries_digest = "
    "sha256 of 'comment_pk,user_pk\\n' lines sorted by comment_pk"
)
_TEXT_IN_REPORT = 80


class GiveawayRules(BaseModel):
    winners: int = 1
    backups: int = 2
    min_mentions: int = 0
    keywords: list[str] = []
    until: datetime | None = None
    max_entries_per_user: int = 1
    exclude: list[str] = []
    min_followers: int = 0
    min_posts: int = 0
    require_avatar: bool = False
    must_follow: list[str] = []
    max_following_to_scan: int = 2000
    max_check_calls: int = 1000

    @field_validator("until")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    @property
    def checks_profiles(self) -> bool:
        return bool(
            self.min_followers
            or self.min_posts
            or self.require_avatar
            or self.must_follow
        )


class UnknownAccountError(Exception):
    """A `must_follow` handle has no public profile."""

    def __init__(self, username: str) -> None:
        super().__init__(f"@{username} has no public profile")
        self.username = username


class Ticket(BaseModel):
    """One comment, and whether it counts as an entry."""

    comment_pk: str
    user: Account
    text: str
    created_at: datetime
    mentions: list[str]
    eligible: bool
    reason: str | None = None
    rank: int | None = None


class Candidate(BaseModel):
    """A drawn ticket and the outcome of its checks."""

    ticket: Ticket
    accepted: bool
    reason: str | None = None


class GiveawayResult(BaseModel):
    """The result of `run_giveaway`."""

    post: PostInfo
    rules: GiveawayRules
    seed: str
    entries_digest: str
    method: str = METHOD
    comments_fetched: int
    tickets: list[Ticket]
    winners: list[Candidate]
    backups: list[Candidate]
    rejected: list[Candidate]
    check_calls: int = 0
    checks_stopped: bool = False
    drawn_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    usage: Usage | None = None

    @computed_field
    @property
    def seed_sha256(self) -> str:
        return hashlib.sha256(self.seed.encode()).hexdigest()

    @computed_field
    @property
    def eligible_users(self) -> int:
        return len({t.user.pk for t in self.tickets if t.eligible})

    def csv_rows(self) -> list[Row]:
        outcome: dict[str, str] = {}
        for label, group in (
            ("winner", self.winners),
            ("backup", self.backups),
        ):
            for candidate in group:
                outcome[candidate.ticket.comment_pk] = label
        for candidate in self.rejected:
            outcome[candidate.ticket.comment_pk] = (
                f"rejected: {candidate.reason}"
            )
        return [
            {
                "rank": t.rank,
                "result": outcome.get(t.comment_pk, ""),
                "username": t.user.username,
                "user_pk": t.user.pk,
                "comment_pk": t.comment_pk,
                "created_at": t.created_at.isoformat(),
                "eligible": t.eligible,
                "reason": t.reason or "",
                "mentions": len(t.mentions),
                "text": t.text,
            }
            for t in self.tickets
        ]

    def markdown(self) -> str:
        return _markdown(self)


def _pk_order(pk: str) -> tuple[int, str]:
    return (int(pk), pk) if pk.isdigit() else (0, pk)


def entries_digest(tickets: Sequence[Ticket]) -> str:
    """SHA-256 over `comment_pk,user_pk` lines, sorted by comment pk."""
    ordered = sorted(tickets, key=lambda t: _pk_order(t.comment_pk))
    lines = "".join(f"{t.comment_pk},{t.user.pk}\n" for t in ordered)
    return hashlib.sha256(lines.encode()).hexdigest()


def rank_key(*, seed: str, comment_pk: str) -> str:
    return hashlib.sha256(f"{seed}:{comment_pk}".encode()).hexdigest()


def estimate_giveaway_calls(*, post: PostInfo, rules: GiveawayRules) -> int:
    """Upper bound: Instagram's comment count includes replies, and winner
    checks may spend up to `max_check_calls`."""
    comments = estimate_calls(
        items=post.comment_count, page_size=COMMENTS_PAGE_SIZE
    )
    checks = rules.max_check_calls if rules.checks_profiles else 0
    return 1 + comments + checks


async def run_giveaway(
    client: AsyncClient,
    *,
    post: PostInfo,
    rules: GiveawayRules,
    seed: str | None = None,
    on_progress: OnProgress | None = None,
) -> GiveawayResult:
    """Fetch every top-level comment, filter, and draw."""
    follow_targets = await _resolve_targets(client, post=post, rules=rules)
    comments = await fetch_comments(client, post=post, on_progress=on_progress)
    tickets = _tickets(comments, post=post, rules=rules)
    eligible = [t for t in tickets if t.eligible]
    seed = seed or secrets.token_hex(16)
    ranked = sorted(
        eligible, key=lambda t: rank_key(seed=seed, comment_pk=t.comment_pk)
    )
    for position, ticket in enumerate(ranked, start=1):
        ticket.rank = position
    winners: list[Candidate] = []
    backups: list[Candidate] = []
    rejected: list[Candidate] = []
    seen_users: set[str] = set()
    check_calls = 0
    checks_stopped = False
    for ticket in ranked:
        if len(winners) + len(backups) >= rules.winners + rules.backups:
            break
        if ticket.user.pk in seen_users:
            continue
        if rules.checks_profiles and check_calls >= rules.max_check_calls:
            checks_stopped = True
            break
        seen_users.add(ticket.user.pk)
        reason, calls = await _check(
            client, ticket=ticket, rules=rules, targets=follow_targets
        )
        check_calls += calls
        if reason:
            rejected.append(
                Candidate(ticket=ticket, accepted=False, reason=reason)
            )
            continue
        group = winners if len(winners) < rules.winners else backups
        group.append(Candidate(ticket=ticket, accepted=True))
    return GiveawayResult(
        post=post,
        rules=rules,
        seed=seed,
        entries_digest=entries_digest(eligible),
        comments_fetched=len(comments),
        tickets=tickets,
        winners=winners,
        backups=backups,
        rejected=rejected,
        check_calls=check_calls,
        checks_stopped=checks_stopped,
    )


def _tickets(
    comments: Sequence[Comment], *, post: PostInfo, rules: GiveawayRules
) -> list[Ticket]:
    host = post.owner.username.lower()
    coauthors = {a.pk for a in post.coauthors}
    excluded = {name.lower() for name in rules.exclude}
    entries: Counter[str] = Counter()
    tickets: list[Ticket] = []
    ordered = sorted(comments, key=lambda c: (c.created_at, _pk_order(c.pk)))
    for comment in ordered:
        mentions = extract_mentions(comment.text)
        reason = _cheap_reason(
            comment,
            mentions=mentions,
            rules=rules,
            host_pk=post.owner.pk,
            host=host,
            coauthors=coauthors,
            excluded=excluded,
        )
        if reason is None:
            if entries[comment.user.pk] >= rules.max_entries_per_user:
                reason = f"entry limit ({rules.max_entries_per_user}) reached"
            else:
                entries[comment.user.pk] += 1
        tickets.append(
            Ticket(
                comment_pk=comment.pk,
                user=comment.user,
                text=comment.text,
                created_at=comment.created_at,
                mentions=mentions,
                eligible=reason is None,
                reason=reason,
            )
        )
    return tickets


def _cheap_reason(
    comment: Comment,
    *,
    mentions: list[str],
    rules: GiveawayRules,
    host_pk: str,
    host: str,
    coauthors: set[str],
    excluded: set[str],
) -> str | None:
    user = comment.user
    if user.pk == host_pk:
        return "host"
    if user.pk in coauthors:
        return "co-author"
    if user.username.lower() in excluded:
        return "excluded"
    if rules.until and comment.created_at > rules.until:
        return "after close"
    text = comment.text.lower()
    for keyword in rules.keywords:
        if keyword.lower() not in text:
            return f"missing {keyword}"
    tagged = [m for m in mentions if m not in (host, user.username.lower())]
    if len(tagged) < rules.min_mentions:
        return f"{len(tagged)} of {rules.min_mentions} mentions"
    return None


async def _resolve_targets(
    client: AsyncClient, *, post: PostInfo, rules: GiveawayRules
) -> dict[str, str]:
    """`--must-follow` handles to pks; the host needs no lookup."""
    targets: dict[str, str] = {}
    for handle in rules.must_follow:
        name = handle.lower()
        if name == post.owner.username.lower():
            targets[name] = post.owner.pk
        else:
            try:
                targets[name] = (await get_profile(client, username=name)).pk
            except ProfileNotFoundError as exc:
                raise UnknownAccountError(name) from exc
    return targets


async def _check(
    client: AsyncClient,
    *,
    ticket: Ticket,
    rules: GiveawayRules,
    targets: dict[str, str],
) -> tuple[str | None, int]:
    """Why a drawn ticket fails the checks (None if it passes), and the
    billed calls the checks made."""
    if not rules.checks_profiles:
        return None, 0
    try:
        profile = await get_profile_by_pk(client, pk=ticket.user.pk)
    except (NotFoundError, PrivateAccountError):
        return "account unavailable", 0
    if profile.follower_count < rules.min_followers:
        need = f"{profile.follower_count:,} < {rules.min_followers:,}"
        return f"followers {need}", 1
    if profile.media_count < rules.min_posts:
        return f"posts {profile.media_count:,} < {rules.min_posts:,}", 1
    if rules.require_avatar and profile.has_anonymous_profile_picture:
        return "no profile picture", 1
    if not targets:
        return None, 1
    if profile.is_private:
        return "private, can't verify follows", 1
    limit = rules.max_following_to_scan
    if profile.following_count > limit:
        return (
            f"follows {profile.following_count:,} accounts, more than the "
            f"{limit:,} checked"
        ), 1
    wanted = set(targets.values())
    walk = await walk_until_complete(
        client,
        edge="following",
        user_pk=profile.pk,
        expected=profile.following_count,
        stop_when=lambda pks: wanted <= pks,
    )
    calls = 1 + walk.pages
    seen = {account.pk for account in walk.accounts}
    missing = [name for name, pk in targets.items() if pk not in seen]
    if not missing:
        return None, calls
    names = ", ".join(f"@{name}" for name in missing)
    if not walk.complete:
        return (
            f"{names} not among the {len(seen):,} of "
            f"{profile.following_count:,} follows Instagram listed"
        ), calls
    return f"doesn't follow {names}", calls


def _short(text: str) -> str:
    flat = " ".join(text.split())
    if len(flat) <= _TEXT_IN_REPORT:
        return flat
    return flat[: _TEXT_IN_REPORT - 1] + "…"


def _candidate_rows(group: Sequence[Candidate]) -> list[list[str]]:
    return [
        [
            str(c.ticket.rank),
            f"@{c.ticket.user.username}",
            _short(c.ticket.text),
            f"{c.ticket.created_at:%Y-%m-%d %H:%M} UTC",
        ]
        for c in group
    ]


def _markdown(result: GiveawayResult) -> str:
    post, rules = result.post, result.rules
    eligible = sum(1 for t in result.tickets if t.eligible)
    drawn = f"{result.drawn_at:%Y-%m-%d %H:%M}"
    lines = [
        f"# Giveaway draw: {post.url}",
        "",
        f"Post by @{post.owner.username}. Drawn {drawn} UTC from "
        f"{result.comments_fetched:,} top-level comments "
        f"(Instagram counts {post.comment_count:,} including replies).",
        "",
        "## Winners",
        "",
    ]
    headers = ["Rank", "Account", "Comment", "Posted"]
    if result.winners:
        lines += md_table(
            headers, _candidate_rows(result.winners), align="rlll"
        )
    else:
        lines += ["No entry passed the checks."]
    if result.backups:
        lines += ["", "## Backups", ""]
        lines += md_table(
            headers, _candidate_rows(result.backups), align="rlll"
        )
    lines += [
        "",
        "## Entries",
        "",
        f"{eligible:,} eligible entries from {result.eligible_users:,} "
        "accounts.",
    ]
    reasons = Counter(t.reason for t in result.tickets if t.reason)
    if reasons:
        lines += [""]
        lines += md_table(
            ["Not counted", "Comments"],
            [[reason, f"{n:,}"] for reason, n in reasons.most_common()],
            align="lr",
        )
    if result.checks_stopped:
        lines += [
            "",
            f"Stopped checking drawn accounts after {result.check_calls:,} "
            "calls, the limit for winner checks. Raise it to keep drawing.",
        ]
    if result.rejected:
        lines += ["", "Drawn but rejected by the checks:", ""]
        lines += md_table(
            ["Rank", "Account", "Reason"],
            [
                [
                    str(c.ticket.rank),
                    f"@{c.ticket.user.username}",
                    c.reason or "",
                ]
                for c in result.rejected
            ],
            align="rll",
        )
    lines += ["", "## Rules", "", *(f"- {rule}" for rule in _rules(rules))]
    lines += [
        "",
        "## Verify this draw",
        "",
        f"- Seed: `{result.seed}`",
        f"- Seed SHA-256: `{result.seed_sha256}`",
        f"- Entries digest (SHA-256): `{result.entries_digest}`",
        f"- Method: {result.method}",
        "",
        "Anyone with the entries CSV can recompute the digest and the ranking.",
        "",
        footer(),
    ]
    return "\n".join(lines) + "\n"


def _rules(rules: GiveawayRules) -> list[str]:
    entries = plural(
        count=rules.max_entries_per_user, word="entry", many="entries"
    )
    lines = [
        f"{plural(count=rules.winners, word='winner')} and "
        f"{plural(count=rules.backups, word='backup')}; one win per account",
        f"Up to {entries} per account",
        "Host and co-authors excluded",
    ]
    if rules.min_mentions:
        tagged = plural(count=rules.min_mentions, word="other account")
        lines.append(f"At least {tagged} tagged")
    lines += [f"Comment contains {k}" for k in rules.keywords]
    if rules.until:
        until = rules.until.astimezone(timezone.utc)
        lines.append(f"Posted before {until:%Y-%m-%d %H:%M} UTC")
    if rules.exclude:
        lines.append("Excluded: " + ", ".join(f"@{n}" for n in rules.exclude))
    if rules.min_followers:
        lines.append(f"Winner has at least {rules.min_followers:,} followers")
    if rules.min_posts:
        lines.append(f"Winner has at least {rules.min_posts:,} posts")
    if rules.require_avatar:
        lines.append("Winner has a profile picture")
    lines += [
        f"Winner follows @{n}; drawn accounts that follow more than "
        f"{rules.max_following_to_scan:,} accounts are skipped"
        for n in rules.must_follow
    ]
    return lines
