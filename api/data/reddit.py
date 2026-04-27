"""Reddit JSON adapter -- public listings only, no auth.
Reddit aggressively rate-limits default User-Agents, so we send a custom UA."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx

from api.data.cache import get_cached, put_cached

REDDIT_TTL = timedelta(minutes=10)
_UA = "ForteResearch/0.1 (research; contact@example.com)"


def get_hot(subreddit: str, *, limit: int = 25) -> list[dict[str, Any]]:
    """Top posts from /r/{sub}/hot. Returns title, score, comments, url, permalink, created_utc."""
    query = {"sub": subreddit, "limit": limit}
    cached = get_cached("reddit_hot", query, REDDIT_TTL)
    if cached is not None:
        return cached["posts"]  # type: ignore[no-any-return]

    url = f"https://www.reddit.com/r/{subreddit}/hot.json"
    r = httpx.get(
        url,
        params={"limit": limit, "raw_json": 1},
        headers={"user-agent": _UA, "accept": "application/json"},
        timeout=15.0,
    )
    r.raise_for_status()
    data = r.json()
    posts: list[dict[str, Any]] = []
    for child in data.get("data", {}).get("children", []):
        d = child.get("data", {})
        if d.get("stickied"):
            continue
        posts.append({
            "title": d.get("title"),
            "score": d.get("score"),
            "num_comments": d.get("num_comments"),
            "url": d.get("url"),  # external link the post points to
            "permalink": f"https://www.reddit.com{d.get('permalink', '')}",
            "subreddit": d.get("subreddit"),
            "selftext": (d.get("selftext") or "")[:400],
        })
    put_cached("reddit_hot", query, {"posts": posts})
    return posts
