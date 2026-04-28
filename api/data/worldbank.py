"""World Bank adapter -- cross-country macro indicators. Free, no key."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx

from api.data.cache import get_cached, put_cached

WB_TTL = timedelta(days=1)
_BASE = "https://api.worldbank.org/v2"
_HEADERS = {"accept": "application/json", "user-agent": "ForteResearch/0.1"}

# Common indicator IDs for quick reference. Full list:
# https://api.worldbank.org/v2/indicator?format=json
COMMON_INDICATORS: dict[str, str] = {
    "gdp_usd": "NY.GDP.MKTP.CD",
    "gdp_growth": "NY.GDP.MKTP.KD.ZG",
    "cpi": "FP.CPI.TOTL.ZG",
    "unemployment": "SL.UEM.TOTL.ZS",
    "debt_to_gdp": "GC.DOD.TOTL.GD.ZS",
    "current_account_pct_gdp": "BN.CAB.XOKA.GD.ZS",
    "fx_reserves_usd": "FI.RES.TOTL.CD",
    "population": "SP.POP.TOTL",
}


def get_series(country: str, indicator: str, *, since: int = 2010) -> list[dict[str, Any]]:
    """Fetch one indicator for one country (ISO-2 or ISO-3 code, e.g. 'US', 'GBR').
    Returns [{year, value}] sorted descending."""
    q = {"country": country.upper(), "indicator": indicator, "since": since}
    cached = get_cached("worldbank", q, WB_TTL)
    if cached is not None:
        return cached["rows"]  # type: ignore[no-any-return]

    r = httpx.get(
        f"{_BASE}/country/{country}/indicator/{indicator}",
        params={"format": "json", "date": f"{since}:2099", "per_page": 100},
        headers=_HEADERS, timeout=20.0,
    )
    r.raise_for_status()
    payload = r.json()
    if not isinstance(payload, list) or len(payload) < 2:
        return []
    rows = [{"year": int(x.get("date")) if x.get("date") else None,
             "value": x.get("value")} for x in payload[1] if x.get("value") is not None]
    put_cached("worldbank", q, {"rows": rows})
    return rows
