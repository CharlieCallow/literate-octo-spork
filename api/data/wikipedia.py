"""Wikipedia REST API adapter. Background and definitional content."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx

from api.data.cache import get_cached, put_cached

WIKI_TTL = timedelta(days=1)
_BASE = "https://en.wikipedia.org/api/rest_v1"
_UA = "ForteResearch/0.1 (https://example.com/forte; contact@example.com)"


def summary(title: str) -> dict[str, Any]:
    """Return the lead-paragraph summary for a Wikipedia article title."""
    query = {"title": title}
    cached = get_cached("wikipedia", query, WIKI_TTL)
    if cached is not None:
        return cached

    safe_title = title.replace(" ", "_")
    url = f"{_BASE}/page/summary/{safe_title}"
    r = httpx.get(url, headers={"user-agent": _UA, "accept": "application/json"}, timeout=15.0)
    r.raise_for_status()
    data = r.json()
    out = {
        "title": data.get("title"),
        "description": data.get("description"),
        "extract": data.get("extract"),
        "url": data.get("content_urls", {}).get("desktop", {}).get("page"),
    }
    put_cached("wikipedia", query, out)
    return out
