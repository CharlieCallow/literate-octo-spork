"""CoinGecko adapter -- crypto prices and market data. Free tier, no key."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx

from api.data.cache import get_cached, put_cached

CG_TTL = timedelta(minutes=10)
_BASE = "https://api.coingecko.com/api/v3"
_HEADERS = {"accept": "application/json", "user-agent": "ForteResearch/0.1"}


def market_overview(*, vs_currency: str = "usd", limit: int = 25) -> list[dict[str, Any]]:
    """Top coins by market cap with price + 24h/7d change + volume."""
    query = {"vs_currency": vs_currency, "limit": limit}
    cached = get_cached("coingecko_markets", query, CG_TTL)
    if cached is not None:
        return cached["coins"]  # type: ignore[no-any-return]

    r = httpx.get(
        f"{_BASE}/coins/markets",
        params={
            "vs_currency": vs_currency, "order": "market_cap_desc",
            "per_page": limit, "page": 1, "sparkline": "false",
            "price_change_percentage": "24h,7d,30d",
        },
        headers=_HEADERS, timeout=15.0,
    )
    r.raise_for_status()
    rows = r.json()
    coins = [{
        "id": c.get("id"), "symbol": c.get("symbol"), "name": c.get("name"),
        "price": c.get("current_price"),
        "market_cap": c.get("market_cap"),
        "volume_24h": c.get("total_volume"),
        "pct_24h": c.get("price_change_percentage_24h_in_currency"),
        "pct_7d": c.get("price_change_percentage_7d_in_currency"),
        "pct_30d": c.get("price_change_percentage_30d_in_currency"),
    } for c in rows]
    put_cached("coingecko_markets", query, {"coins": coins})
    return coins


def trending() -> list[dict[str, Any]]:
    """Top 7 trending coins on CoinGecko search (last 24h)."""
    cached = get_cached("coingecko_trending", {"v": 1}, CG_TTL)
    if cached is not None:
        return cached["coins"]  # type: ignore[no-any-return]
    r = httpx.get(f"{_BASE}/search/trending", headers=_HEADERS, timeout=15.0)
    r.raise_for_status()
    rows = r.json().get("coins", [])
    coins = [{
        "id": x.get("item", {}).get("id"),
        "symbol": x.get("item", {}).get("symbol"),
        "name": x.get("item", {}).get("name"),
        "market_cap_rank": x.get("item", {}).get("market_cap_rank"),
    } for x in rows]
    put_cached("coingecko_trending", {"v": 1}, {"coins": coins})
    return coins
