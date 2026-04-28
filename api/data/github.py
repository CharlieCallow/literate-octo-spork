"""GitHub adapter -- repo metadata + recent commit activity. Free, optional token."""

from __future__ import annotations

import os
from datetime import timedelta
from typing import Any

import httpx

from api.data.cache import get_cached, put_cached

GH_TTL = timedelta(hours=2)
_BASE = "https://api.github.com"


def _headers() -> dict[str, str]:
    h = {"accept": "application/vnd.github+json", "user-agent": "ForteResearch/0.1"}
    if token := os.environ.get("GITHUB_TOKEN"):
        h["authorization"] = f"Bearer {token}"
    return h


def repo_summary(owner: str, repo: str) -> dict[str, Any]:
    query = {"owner": owner, "repo": repo}
    cached = get_cached("github_repo", query, GH_TTL)
    if cached is not None:
        return cached
    r = httpx.get(f"{_BASE}/repos/{owner}/{repo}", headers=_headers(), timeout=15.0)
    r.raise_for_status()
    d = r.json()
    out = {
        "full_name": d.get("full_name"),
        "description": d.get("description"),
        "stars": d.get("stargazers_count"),
        "forks": d.get("forks_count"),
        "watchers": d.get("subscribers_count"),
        "open_issues": d.get("open_issues_count"),
        "language": d.get("language"),
        "topics": d.get("topics") or [],
        "pushed_at": d.get("pushed_at"),
        "updated_at": d.get("updated_at"),
        "url": d.get("html_url"),
    }
    put_cached("github_repo", query, out)
    return out


def search_repos(query: str, *, sort: str = "stars", limit: int = 10) -> list[dict[str, Any]]:
    """Search repos. `sort` is 'stars' | 'forks' | 'updated' | 'help-wanted-issues'."""
    q = {"q": query, "sort": sort, "limit": limit}
    cached = get_cached("github_search", q, GH_TTL)
    if cached is not None:
        return cached["repos"]  # type: ignore[no-any-return]
    r = httpx.get(
        f"{_BASE}/search/repositories",
        params={"q": query, "sort": sort, "order": "desc", "per_page": min(limit, 30)},
        headers=_headers(), timeout=15.0,
    )
    r.raise_for_status()
    rows = r.json().get("items", []) or []
    repos = [{
        "full_name": d.get("full_name"),
        "description": d.get("description"),
        "stars": d.get("stargazers_count"),
        "language": d.get("language"),
        "url": d.get("html_url"),
        "pushed_at": d.get("pushed_at"),
    } for d in rows[:limit]]
    put_cached("github_search", q, {"repos": repos})
    return repos
