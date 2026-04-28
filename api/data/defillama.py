"""DeFiLlama adapter -- DeFi protocol TVL + chain metrics. Free, no key."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx

from api.data.cache import get_cached, put_cached

DL_TTL = timedelta(minutes=15)
_BASE = "https://api.llama.fi"
_HEADERS = {"accept": "application/json", "user-agent": "ForteResearch/0.1"}


def top_protocols(limit: int = 25) -> list[dict[str, Any]]:
    """Top DeFi protocols by TVL."""
    q = {"limit": limit}
    cached = get_cached("defillama_protocols", q, DL_TTL)
    if cached is not None:
        return cached["rows"]  # type: ignore[no-any-return]

    r = httpx.get(f"{_BASE}/protocols", headers=_HEADERS, timeout=20.0)
    r.raise_for_status()
    rows = r.json() or []
    rows = sorted(rows, key=lambda p: p.get("tvl") or 0, reverse=True)[:limit]
    out = [{
        "name": p.get("name"),
        "category": p.get("category"),
        "chain": p.get("chain"),
        "tvl": p.get("tvl"),
        "change_1d": p.get("change_1d"),
        "change_7d": p.get("change_7d"),
        "url": p.get("url"),
    } for p in rows]
    put_cached("defillama_protocols", q, {"rows": out})
    return out


def chain_tvl(limit: int = 20) -> list[dict[str, Any]]:
    """TVL by chain."""
    q = {"limit": limit}
    cached = get_cached("defillama_chains", q, DL_TTL)
    if cached is not None:
        return cached["rows"]  # type: ignore[no-any-return]
    r = httpx.get(f"{_BASE}/v2/chains", headers=_HEADERS, timeout=15.0)
    r.raise_for_status()
    rows = r.json() or []
    rows = sorted(rows, key=lambda c: c.get("tvl") or 0, reverse=True)[:limit]
    out = [{
        "name": c.get("name"),
        "tvl": c.get("tvl"),
        "token_symbol": c.get("tokenSymbol"),
    } for c in rows]
    put_cached("defillama_chains", q, {"rows": out})
    return out
