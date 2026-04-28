"""arXiv adapter -- research-paper search via the Atom export API."""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

import httpx

from api.data.cache import get_cached, put_cached

AX_TTL = timedelta(hours=6)
_BASE = "http://export.arxiv.org/api/query"
_HEADERS = {"user-agent": "ForteResearch/0.1"}


_TITLE_RE   = re.compile(r"<title>(.+?)</title>", re.S)
_SUMMARY_RE = re.compile(r"<summary>(.+?)</summary>", re.S)
_ID_RE      = re.compile(r"<id>(http[^<]+)</id>")
_AUTHOR_RE  = re.compile(r"<author>\s*<name>([^<]+)</name>", re.S)
_PUBLISHED_RE = re.compile(r"<published>([^<]+)</published>")


def search(query: str, *, limit: int = 10) -> list[dict[str, Any]]:
    """Search arXiv by free-text query. Returns id/title/summary/authors/published."""
    q = {"q": query, "limit": limit}
    cached = get_cached("arxiv", q, AX_TTL)
    if cached is not None:
        return cached["papers"]  # type: ignore[no-any-return]

    r = httpx.get(
        _BASE,
        params={"search_query": f"all:{query}", "start": 0, "max_results": limit,
                "sortBy": "submittedDate", "sortOrder": "descending"},
        headers=_HEADERS, timeout=20.0,
    )
    r.raise_for_status()

    # arXiv returns Atom XML. Lightweight regex parse: each <entry> after the
    # opening feed metadata holds one paper. Split on </entry> and extract.
    body = r.text.split("<entry>")[1:]
    papers: list[dict[str, Any]] = []
    for chunk in body[:limit]:
        title_m = _TITLE_RE.search(chunk)
        summary_m = _SUMMARY_RE.search(chunk)
        id_m = _ID_RE.search(chunk)
        published_m = _PUBLISHED_RE.search(chunk)
        authors = _AUTHOR_RE.findall(chunk)
        papers.append({
            "id": id_m.group(1) if id_m else None,
            "title": (title_m.group(1).strip() if title_m else "").replace("\n", " "),
            "summary": (summary_m.group(1).strip()[:400] if summary_m else ""),
            "authors": authors[:5],
            "published": published_m.group(1) if published_m else None,
            "url": id_m.group(1) if id_m else None,
        })
    put_cached("arxiv", q, {"papers": papers})
    return papers
