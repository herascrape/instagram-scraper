"""Synthetic payloads shaped like the live herascrape responses."""

from instagram_scrapers.instagram import JSON


def compact_user(
    pk: str,
    username: str | None = None,
    *,
    full_name: str = "Test Person",
    is_private: bool = False,
    is_verified: bool = False,
    anonymous_pic: bool = False,
) -> JSON:
    return {
        "pk": pk,
        "pk_id": pk,
        "id": pk,
        "username": username or f"user_{pk}",
        "full_name": full_name,
        "is_private": is_private,
        "is_verified": is_verified,
        "has_anonymous_profile_picture": anonymous_pic,
        "profile_pic_url": "https://example.com/pic.jpg",
        "latest_reel_media": 0,
        "account_badges": [],
    }


def full_profile(
    pk: str,
    username: str | None = None,
    *,
    followers: int = 100,
    following: int = 100,
    posts: int = 10,
    biography: str = "hello",
    external_url: str = "",
    account_type: int = 1,
    full_name: str = "Test Person",
    is_private: bool = False,
    is_verified: bool = False,
    anonymous_pic: bool = False,
) -> JSON:
    return {
        **compact_user(
            pk,
            username,
            full_name=full_name,
            is_private=is_private,
            is_verified=is_verified,
            anonymous_pic=anonymous_pic,
        ),
        "follower_count": followers,
        "following_count": following,
        "media_count": posts,
        "biography": biography,
        "external_url": external_url,
        "bio_links": [],
        "account_type": account_type,
        "is_business": account_type == 2,
        "category": None,
    }


def list_page(items: list[JSON], *, next_cursor: str | None) -> JSON:
    return {
        "items": items,
        "next_cursor": next_cursor,
        "has_more": next_cursor is not None,
    }


def post_detail(
    pk: str,
    *,
    code: str | None = None,
    taken_at: int = 1_790_000_000,
    product_type: str = "feed",
    media_type: int = 1,
    likes: int | None = 10,
    comments: int = 2,
    plays: int | None = None,
    caption: str = "",
) -> JSON:
    return {
        "pk": pk,
        "id": f"{pk}_1",
        "code": code or f"C{pk}",
        "taken_at": taken_at,
        "product_type": product_type,
        "media_type": media_type,
        "like_count": likes,
        "comment_count": comments,
        "play_count": plays,
        "like_and_view_counts_disabled": likes is None,
        "caption": {"text": caption} if caption else None,
    }


def grid_page(
    posts: list[JSON],
    *,
    cursor: str | None,
    pinned: set[str] | None = None,
    owner_pk: str = "9",
) -> JSON:
    """`pinned` media pks carry the owner in `timeline_pinned_user_ids`."""
    grid: list[JSON] = [
        {
            "item_type": "media",
            "media": {
                "pk": p["pk"],
                "id": p["id"],
                "taken_at": p.get("taken_at"),
                "timeline_pinned_user_ids": (
                    [owner_pk] if p["pk"] in (pinned or set()) else []
                ),
            },
        }
        for p in posts
    ]
    feed: JSON = {
        "more_available": cursor is not None,
        "next_max_id": "ignored",
        "profile_grid_items": grid,
        "profile_grid_items_cursor": cursor,
        "pinned_profile_grid_items_ids": "",
    }
    return {"feed": feed, "posts": {str(p["id"]): p for p in posts}}


def comment(
    pk: str,
    *,
    user: JSON,
    text: str,
    created_at: int = 1_790_000_000,
) -> JSON:
    return {
        "pk": pk,
        "text": text,
        "created_at": created_at,
        "user_id": user["pk"],
        "user": user,
        "child_comment_count": 0,
    }


def comments_page(comments: list[JSON], *, next_min_id: str | None) -> JSON:
    return {
        "comments": comments,
        "has_more": next_min_id is not None,
        "has_more_comments": False,
        "next_min_id": next_min_id,
        "comment_count": len(comments),
    }


def post_body(
    pk: str,
    *,
    code: str,
    owner: JSON,
    comment_count: int = 0,
    coauthors: list[JSON] | None = None,
    taken_at: int = 1_790_000_000,
    caption: str = "Giveaway!",
) -> JSON:
    return {
        "pk": pk,
        "id": f"{pk}_{owner['pk']}",
        "code": code,
        "taken_at": taken_at,
        "user": owner,
        "comment_count": comment_count,
        "coauthor_producers": coauthors or [],
        "caption": {"text": caption},
    }
