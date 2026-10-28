"""Typed views over the Instagram payloads herascrape returns."""

import math
import re
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timezone
from typing import Literal, cast
from urllib.parse import urlsplit

from herascrape import AsyncClient
from pydantic import BaseModel, ConfigDict, model_validator

JSON = dict[str, object]
OnProgress = Callable[[int], None]
PostKind = Literal["reel", "carousel", "photo", "video"]

FOLLOWERS_PAGE_SIZE = 25
POSTS_PAGE_SIZE = 9
COMMENTS_PAGE_SIZE = 15
# A walk that returns at least this share of follower_count is the whole
# list; counts drift while Instagram is paged.
COMPLETE_SHARE = 0.98

_USERNAME = re.compile(r"^[a-z0-9._]{1,30}$")
_SHORTCODE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")
_MEDIA_PK = re.compile(r"^\d+(?:_\d+)?$")
_POST_SEGMENTS = {"p", "reel", "reels", "tv"}
_RESERVED = _POST_SEGMENTS | {"stories", "explore", "accounts", "direct"}


class _IGModel(BaseModel):
    """Tolerant base: Instagram nulls fall back to field defaults."""

    model_config = ConfigDict(coerce_numbers_to_str=True)

    @model_validator(mode="before")
    @classmethod
    def _drop_nulls(cls, data: object) -> object:
        if isinstance(data, dict):
            items = cast("dict[str, object]", data).items()
            return {key: value for key, value in items if value is not None}
        return data


class Account(_IGModel):
    """The compact user object of follower, following and comment lists."""

    pk: str
    username: str
    full_name: str = ""
    is_private: bool = False
    is_verified: bool = False
    has_anonymous_profile_picture: bool | None = None

    @classmethod
    def from_api(cls, data: JSON) -> "Account":
        return cls.model_validate(data)


class Profile(Account):
    """A full profile."""

    follower_count: int = 0
    following_count: int = 0
    media_count: int = 0
    biography: str = ""
    external_url: str = ""
    has_bio_links: bool = False
    account_type: int | None = None
    is_business: bool = False
    category: str | None = None

    @classmethod
    def from_api(cls, data: JSON) -> "Profile":
        return cls.model_validate(
            {**data, "has_bio_links": bool(data.get("bio_links"))}
        )

    @property
    def url(self) -> str:
        return f"https://www.instagram.com/{self.username}/"


class Post(BaseModel):
    """One post of a profile grid."""

    pk: str
    code: str
    taken_at: datetime
    kind: PostKind
    like_count: int | None
    comment_count: int
    play_count: int | None
    caption: str
    url: str


class PostInfo(BaseModel):
    """A single post as `/posts/{id}` returns it."""

    pk: str
    code: str
    url: str
    owner: Account
    coauthors: list[Account]
    comment_count: int
    taken_at: datetime
    caption: str


class Comment(BaseModel):
    """A top-level comment."""

    pk: str
    text: str
    created_at: datetime
    user: Account


class FollowerList(BaseModel):
    """Followers as listed, and whether Instagram stopped short."""

    accounts: list[Account]
    capped: bool


def parse_username(value: str) -> str:
    """A username from `name`, `@name` or a profile URL."""
    text = value.strip()
    if "instagram.com" in text:
        segments = _url_segments(text)
        if not segments or segments[0] in _RESERVED:
            raise ValueError(f"not a profile URL: {value!r}")
        text = segments[0]
    username = text.removeprefix("@").lower()
    if not _USERNAME.match(username):
        raise ValueError(f"not an Instagram username: {value!r}")
    return username


def parse_post_ref(value: str) -> str:
    """A shortcode or media pk from a post/reel URL, shortcode or pk."""
    text = value.strip()
    if "instagram.com" in text or "/" in text:
        segments = _url_segments(text)
        for index, segment in enumerate(segments[:-1]):
            if segment in _POST_SEGMENTS:
                text = segments[index + 1]
                break
        else:
            raise ValueError(f"not a post or reel URL: {value!r}")
    if _MEDIA_PK.match(text) or _SHORTCODE.match(text):
        return text
    raise ValueError(f"not a post shortcode, id or URL: {value!r}")


def _url_segments(text: str) -> list[str]:
    url = text if "://" in text else f"https://{text}"
    return [part for part in urlsplit(url).path.split("/") if part]


def estimate_calls(*, items: int, page_size: int) -> int:
    """Pages needed to list `items`; an empty list still costs one call."""
    return max(1, math.ceil(items / page_size))


def _timestamp(value: object) -> datetime:
    seconds = value if isinstance(value, (int, float)) else 0
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


def _caption(data: JSON) -> str:
    caption = data.get("caption")
    if isinstance(caption, dict):
        text = cast(JSON, caption).get("text")
        return text if isinstance(text, str) else ""
    return ""


def _int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _post_url(*, code: str, product_type: object) -> str:
    kind = "reel" if product_type == "clips" else "p"
    return f"https://www.instagram.com/{kind}/{code}/"


def _kind(data: JSON) -> PostKind:
    if data.get("product_type") == "clips":
        return "reel"
    if data.get("product_type") == "carousel_container":
        return "carousel"
    media_type = data.get("media_type")
    if media_type == 8:
        return "carousel"
    return "video" if media_type == 2 else "photo"


class PrivateProfileError(Exception):
    """The target is private, and this tool never logs in."""

    def __init__(self, username: str) -> None:
        super().__init__(f"@{username} is private")
        self.username = username


async def get_profile(client: AsyncClient, *, username: str) -> Profile:
    return Profile.from_api(await client.user_by_username(username))


async def get_public_profile(client: AsyncClient, *, username: str) -> Profile:
    """The profile, or `PrivateProfileError` before any list is paged."""
    profile = await get_profile(client, username=username)
    if profile.is_private:
        raise PrivateProfileError(profile.username)
    return profile


async def get_profile_by_pk(client: AsyncClient, *, pk: str) -> Profile:
    return Profile.from_api(await client.user(pk))


async def get_post(client: AsyncClient, *, ref: str) -> PostInfo:
    data: JSON = await client.post(ref)
    owner = cast(JSON, data["user"])
    coauthors = cast("list[JSON]", data.get("coauthor_producers") or [])
    code = str(data["code"])
    return PostInfo(
        pk=str(data["pk"]),
        code=code,
        url=_post_url(code=code, product_type=data.get("product_type")),
        owner=Account.from_api(owner),
        coauthors=[Account.from_api(user) for user in coauthors],
        comment_count=_int(data.get("comment_count")) or 0,
        taken_at=_timestamp(data.get("taken_at")),
        caption=_caption(data),
    )


async def fetch_followers(
    client: AsyncClient,
    *,
    profile: Profile,
    limit: int | None,
    on_progress: OnProgress | None = None,
) -> FollowerList:
    """The first `limit` unique followers (all if None), newest first.

    Pages can overlap, so accounts are deduplicated by pk, and a page that
    adds nothing new ends the walk.
    """
    seen: dict[str, Account] = {}
    cursor: str | None = None
    while True:
        page: JSON = await client.user_followers(profile.pk, cursor=cursor)
        added = 0
        for item in cast("list[JSON]", page.get("items") or []):
            account = Account.from_api(item)
            if account.pk not in seen:
                seen[account.pk] = account
                added += 1
            if limit is not None and len(seen) >= limit:
                return FollowerList(accounts=list(seen.values()), capped=False)
        if on_progress:
            on_progress(len(seen))
        next_cursor = page.get("next_cursor")
        if not page.get("has_more") or not isinstance(next_cursor, str):
            break
        if not added:
            break
        cursor = next_cursor
    capped = len(seen) < COMPLETE_SHARE * profile.follower_count
    return FollowerList(accounts=list(seen.values()), capped=capped)


async def fetch_following(
    client: AsyncClient, *, user_pk: str
) -> AsyncIterator[Account]:
    """Accounts `user_pk` follows, lazily, so a scan can stop early."""
    async for item in client.iter_user_following(user_pk):
        yield Account.from_api(item)


async def fetch_posts(
    client: AsyncClient, *, user_pk: str, limit: int
) -> list[Post]:
    """The newest `limit` grid posts, pinned posts left out."""
    posts: list[Post] = []
    cursor: str | None = None
    while len(posts) < limit:
        page: JSON = await client.user_posts(user_pk, cursor=cursor)
        feed = cast(JSON, page.get("feed") or {})
        details = cast("dict[str, JSON]", page.get("posts") or {})
        grid = cast("list[JSON]", feed.get("profile_grid_items") or [])
        for item in grid:
            media = cast(JSON, item.get("media") or {})
            post = _grid_post(media=media, details=details)
            if post is not None and len(posts) < limit:
                posts.append(post)
        next_cursor = feed.get("profile_grid_items_cursor")
        if not feed.get("more_available") or not isinstance(next_cursor, str):
            break
        cursor = next_cursor
    return posts


def _grid_post(*, media: JSON, details: dict[str, JSON]) -> Post | None:
    if media.get("timeline_pinned_user_ids"):
        return None
    media_id, media_pk = str(media.get("id")), str(media.get("pk"))
    detail = details.get(media_id)
    if detail is None or not isinstance(detail.get("code"), str):
        return None
    code = str(detail["code"])
    taken_at = (
        detail.get("taken_at")
        or detail.get("1ltaken_at")
        or media.get("taken_at")
    )
    return Post(
        pk=media_pk,
        code=code,
        taken_at=_timestamp(taken_at),
        kind=_kind(detail),
        like_count=_int(detail.get("like_count")),
        comment_count=_int(detail.get("comment_count")) or 0,
        play_count=_int(detail.get("play_count")),
        caption=_caption(detail),
        url=_post_url(code=code, product_type=detail.get("product_type")),
    )


async def fetch_comments(
    client: AsyncClient,
    *,
    post: PostInfo,
    on_progress: OnProgress | None = None,
) -> list[Comment]:
    """Every top-level comment, deduplicated, in the order served."""
    seen: dict[str, Comment] = {}
    min_id: str | None = None
    while True:
        page: JSON = await client.post_comments(post.pk, min_id=min_id)
        for item in cast("list[JSON]", page.get("comments") or []):
            parsed = _comment(item)
            seen.setdefault(parsed.pk, parsed)
        if on_progress:
            on_progress(len(seen))
        next_min_id = page.get("next_min_id")
        if (
            not page.get("has_more")
            or not isinstance(next_min_id, str)
            or next_min_id == min_id
        ):
            return list(seen.values())
        min_id = next_min_id


def _comment(data: JSON) -> Comment:
    text = data.get("text")
    return Comment(
        pk=str(data["pk"]),
        text=text if isinstance(text, str) else "",
        created_at=_timestamp(data.get("created_at")),
        user=Account.from_api(cast(JSON, data["user"])),
    )
