"""Fake and ghost follower signals. Weights are documented in the README."""

import re
from collections.abc import Sequence
from typing import Literal

from .instagram import Account, Profile

Signal = Literal[
    "no_avatar",
    "no_posts",
    "few_posts",
    "follow_ratio",
    "mass_following",
    "generated_username",
    "no_name",
    "empty_bio",
]
Category = Literal["real", "ghost", "fake"]

SIGNAL_LABELS: dict[Signal, str] = {
    "no_avatar": "no profile picture",
    "no_posts": "no posts",
    "few_posts": "fewer than 10 posts",
    "follow_ratio": "follows 10x more accounts than follow it (500+)",
    "mass_following": "follows 5,000+ accounts",
    "generated_username": "username looks generated",
    "no_name": "no display name",
    "empty_bio": "empty bio and no link",
}

BOT_WEIGHTS: dict[Signal, int] = {
    "follow_ratio": 3,
    "mass_following": 1,
    "generated_username": 1,
    "no_name": 1,
}
INACTIVE_WEIGHTS: dict[Signal, int] = {
    "no_avatar": 2,
    "no_posts": 2,
    "few_posts": 1,
    "empty_bio": 1,
}
FEW_POSTS = 10
FAKE_MIN_BOT = 3
FAKE_MIN_TOTAL = 5
GHOST_MIN_INACTIVE = 3

_TRAILING_DIGITS = re.compile(r"\d{4,}$")


def looks_generated(username: str) -> bool:
    """Four or more trailing digits, or digits making up 40%+ of it."""
    digits = sum(char.isdigit() for char in username)
    return bool(_TRAILING_DIGITS.search(username)) or (
        digits / max(len(username), 1) >= 0.4
    )


def compact_signals(account: Account) -> list[Signal]:
    """Signals readable from a follower-list item, without a profile call."""
    signals: list[Signal] = []
    if account.has_anonymous_profile_picture:
        signals.append("no_avatar")
    if looks_generated(account.username):
        signals.append("generated_username")
    if not account.full_name.strip():
        signals.append("no_name")
    return signals


def profile_signals(profile: Profile) -> list[Signal]:
    following, followers = profile.following_count, profile.follower_count
    checks: list[tuple[Signal, bool]] = [
        ("no_avatar", profile.has_anonymous_profile_picture is True),
        ("no_posts", profile.media_count == 0),
        ("few_posts", 1 <= profile.media_count < FEW_POSTS),
        (
            "follow_ratio",
            following >= 500 and following >= 10 * max(followers, 1),
        ),
        ("mass_following", following >= 5000),
        ("generated_username", looks_generated(profile.username)),
        ("no_name", not profile.full_name.strip()),
        (
            "empty_bio",
            not profile.biography.strip()
            and not profile.external_url
            and not profile.has_bio_links,
        ),
    ]
    return [signal for signal, present in checks if present]


def classify(
    *, signals: Sequence[Signal], verified: bool
) -> tuple[Category, int]:
    """The category and total score for one account's signals."""
    bot = sum(BOT_WEIGHTS.get(signal, 0) for signal in signals)
    inactive = sum(INACTIVE_WEIGHTS.get(signal, 0) for signal in signals)
    total = bot + inactive
    if verified:
        return "real", total
    posted_little = "no_posts" in signals or "few_posts" in signals
    looks_unused = posted_little or "no_avatar" in signals
    if bot >= FAKE_MIN_BOT and total >= FAKE_MIN_TOTAL and looks_unused:
        return "fake", total
    if inactive >= GHOST_MIN_INACTIVE and posted_little:
        return "ghost", total
    return "real", total
