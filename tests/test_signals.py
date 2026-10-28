import pytest

from instagram_scrapers.instagram import Account, Profile
from instagram_scrapers.signals import (
    Category,
    Signal,
    classify,
    compact_signals,
    looks_generated,
    profile_signals,
)
from tests.factories import compact_user, full_profile


@pytest.mark.parametrize(
    ("username", "expected"),
    [
        ("anna_k", False),
        ("bakery.daily", False),
        ("user84629173", True),
        ("j0hn5555", True),
        ("a1b2c3d4", True),
        ("maria_22", False),
    ],
)
def test_looks_generated(username: str, expected: bool) -> None:
    assert looks_generated(username) is expected


@pytest.mark.parametrize(
    ("signals", "verified", "expected"),
    [
        (["follow_ratio", "generated_username", "no_name"], False, "fake"),
        (["follow_ratio", "no_posts"], False, "fake"),
        (["follow_ratio"], False, "real"),
        (["no_posts", "empty_bio"], False, "ghost"),
        (["no_avatar", "no_posts"], False, "ghost"),
        (["no_avatar", "empty_bio"], False, "real"),
        (["few_posts", "no_avatar"], False, "ghost"),
        (["few_posts"], False, "real"),
        (["follow_ratio", "generated_username", "no_name"], True, "real"),
        ([], False, "real"),
    ],
)
def test_classify_table(
    signals: list[Signal], verified: bool, expected: Category
) -> None:
    category, _ = classify(signals=signals, verified=verified)
    assert category == expected


def test_classify_score_sums_weights() -> None:
    _, score = classify(
        signals=["follow_ratio", "no_avatar", "empty_bio"], verified=False
    )
    assert score == 6


def test_profile_signals() -> None:
    bot = Profile.from_api(
        full_profile(
            "1",
            "user84629173",
            followers=12,
            following=6000,
            posts=0,
            biography="",
            full_name="",
            anonymous_pic=True,
        )
    )
    assert profile_signals(bot) == [
        "no_avatar",
        "no_posts",
        "follow_ratio",
        "mass_following",
        "generated_username",
        "no_name",
        "empty_bio",
    ]


def test_profile_signals_ratio_needs_500_following() -> None:
    small = Profile.from_api(full_profile("1", followers=1, following=400))
    assert "follow_ratio" not in profile_signals(small)


def test_compact_signals() -> None:
    account = Account.from_api(
        compact_user("1", "user84629173", full_name=" ", anonymous_pic=True)
    )
    assert compact_signals(account) == [
        "no_avatar",
        "generated_username",
        "no_name",
    ]
