"""Bulk URL reachability check.

Used at render time to drop dead links from the Sources section. Inline
citation anchors that resolve to an unreachable URL are stripped (the visible
anchor text remains so the reader can still see what the model was referring
to). Results are cached in the data_cache table for a day so we don't keep
re-HEADing the same URLs across reports."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

import httpx

from api.data.cache import get_cached, put_cached

log = logging.getLogger("url_check")

_TTL = timedelta(days=1)
_TIMEOUT = 5.0
_CACHE_SOURCE = "url_reachable"


def _cache_key(url: str) -> dict[str, str]:
    return {"url": url}


async def _check_one(client: httpx.AsyncClient, url: str) -> tuple[str, bool]:
    try:
        r = await client.head(url)
    except (TimeoutError, httpx.HTTPError):
        return url, False
    if r.status_code < 400:
        return url, True
    # Some servers reject HEAD; fall back to GET (no body to read; we only need the status line).
    if r.status_code in (401, 403, 405, 501):
        try:
            r = await client.get(url)
        except (TimeoutError, httpx.HTTPError):
            return url, False
        return url, r.status_code < 400
    return url, False


async def _check_bulk_async(urls: list[str]) -> dict[str, bool]:
    """Issue HEAD requests in parallel; return {url: reachable}."""
    if not urls:
        return {}
    async with httpx.AsyncClient(
        timeout=_TIMEOUT,
        follow_redirects=True,
        headers={"user-agent": "ForteResearch/0.1 link-check"},
    ) as client:
        results = await asyncio.gather(*[_check_one(client, u) for u in urls])
    return dict(results)


def check_reachable(urls: list[str]) -> set[str]:
    """Return the subset of `urls` that are reachable.

    Cached per-URL for a day in `data_cache` so we don't repeat work across
    reports. Cache misses are HEAD-checked in parallel."""
    if not urls:
        return set()

    deduped = list(dict.fromkeys(urls))
    reachable: set[str] = set()
    to_check: list[str] = []

    for url in deduped:
        cached = get_cached(_CACHE_SOURCE, _cache_key(url), _TTL)
        if cached is not None:
            if cached.get("ok"):
                reachable.add(url)
        else:
            to_check.append(url)

    if to_check:
        try:
            results = asyncio.run(_check_bulk_async(to_check))
        except RuntimeError:
            # If we're already inside an event loop (rare in workers), do the
            # checks one by one synchronously.
            results = {}
            with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
                for url in to_check:
                    try:
                        r = client.head(url)
                        ok = r.status_code < 400 or (
                            r.status_code in (401, 403, 405, 501)
                            and client.get(url).status_code < 400
                        )
                    except httpx.HTTPError:
                        ok = False
                    results[url] = ok
        for url, ok in results.items():
            put_cached(_CACHE_SOURCE, _cache_key(url), {"ok": ok})
            if ok:
                reachable.add(url)
        log.info("url-check: %d/%d reachable", len(reachable & set(to_check)), len(to_check))

    return reachable
