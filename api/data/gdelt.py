"""GDELT adapter -- global news event search. Free, no key."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx

from api.data.cache import get_cached, put_cached

GD_TTL = timedelta(minutes=30)
_BASE = "https://api.gdeltproject.org/api/v2/doc/doc"
_HEADERS = {"accept": "application/json", "user-agent": "ForteResearch/0.1"}


def search_articles(query: str, *, days: int = 3, limit: int = 15) -> list[dict[str, Any]]:
    """Search recent global news articles. Returns title/url/source/seendate.
    `days` controls the lookback window (1-7 reasonable)."""
    q = {"q": query, "days": days, "limit": limit}
    cached = get_cached("gdelt", q, GD_TTL)
    if cached is not None:
        return cached["articles"]  # type: ignore[no-any-return]

    r = httpx.get(
        _BASE,
        params={
            "query": query,
            "mode": "ArtList",
            "maxrecords": min(limit, 50),
            "sort": "DateDesc",
            "format": "json",
            "timespan": f"{max(1, min(days, 7))}d",
        },
        headers=_HEADERS, timeout=20.0,
    )
    r.raise_for_status()
    rows = r.json().get("articles", []) or []
    articles = [{
        "title": a.get("title"),
        "url": a.get("url"),
        "source": a.get("domain") or a.get("sourcecountry"),
        "seendate": a.get("seendate"),
        "language": a.get("language"),
    } for a in rows[:limit]]
    put_cached("gdelt", q, {"articles": articles})
    return articles
