"""Hacker News adapter via the Algolia search API. No auth, no rate limit issues."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx

from api.data.cache import get_cached, put_cached

HN_TTL = timedelta(minutes=10)
_BASE = "https://hn.algolia.com/api/v1"


def search(
    query: str,
    *,
    tags: str = "story",
    hits: int = 20,
    numeric_filters: str | None = None,
) -> list[dict[str, Any]]:
    """Search HN. Defaults to story-tagged matches; pass tags='front_page' for the homepage."""
    q = {"query": query, "tags": tags, "hits": hits, "filters": numeric_filters}
    cached = get_cached("hn_search", q, HN_TTL)
    if cached is not None:
        return cached["stories"]  # type: ignore[no-any-return]

    params: dict[str, Any] = {"query": query, "tags": tags, "hitsPerPage": hits}
    if numeric_filters:
        params["numericFilters"] = numeric_filters
    r = httpx.get(f"{_BASE}/search", params=params, timeout=15.0)
    r.raise_for_status()
    data = r.json()
    stories: list[dict[str, Any]] = []
    for h in data.get("hits", []):
        stories.append({
            "title": h.get("title") or h.get("story_title"),
            "points": h.get("points"),
            "num_comments": h.get("num_comments"),
            "url": h.get("url"),
            "hn_url": f"https://news.ycombinator.com/item?id={h.get('objectID')}",
            "author": h.get("author"),
            "created_at": h.get("created_at"),
        })
    put_cached("hn_search", q, {"stories": stories})
    return stories
