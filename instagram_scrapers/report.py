"""Markdown building blocks shared by the reports."""

from collections.abc import Sequence

from .instagram import Profile

REPO_URL = "https://github.com/herascrape/instagram-scraper"
_ALIGN = {"l": ":---", "r": "---:", "c": ":---:"}


def md_table(
    headers: Sequence[str], rows: Sequence[Sequence[str]], *, align: str
) -> list[str]:
    """A GitHub Markdown table; `align` has one of l/r/c per column."""

    def cells(values: Sequence[str]) -> str:
        escaped = (
            v.replace("|", "\\|").replace("\r", " ").replace("\n", " ")
            for v in values
        )
        return "| " + " | ".join(escaped) + " |"

    return [
        cells(headers),
        "| " + " | ".join(_ALIGN[a] for a in align) + " |",
        *(cells(row) for row in rows),
    ]


def percent(part: float, whole: float) -> str:
    if not whole:
        return "n/a"
    share = 100 * part / whole
    return f"{share:.1f}%" if 0 < share < 1 else f"{share:.0f}%"


def cap_note(*, profile: Profile, listed: int) -> str:
    """Why fewer followers were listed than the profile counts."""
    if profile.is_business or profile.is_verified or profile.account_type == 2:
        return (
            f"Instagram lists only the newest {listed} followers of this "
            "account (it does this for business and verified accounts), so "
            "the results cover those."
        )
    return (
        f"Instagram listed {listed:,} of {profile.follower_count:,} "
        "followers; the rest were not returned."
    )


def footer() -> str:
    return f"Generated with [instagram-scraper]({REPO_URL})."
