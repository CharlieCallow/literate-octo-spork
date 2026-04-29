"""CFTC Commitments of Traders adapter.

The CFTC publishes weekly futures-positioning data via the Socrata
public API on publicreporting.cftc.gov. Free, no key. We use the
Legacy Futures-Only report ('jun7-fc8e') because it's the broadest --
one row per market per week with managed-money / commercial /
non-commercial nets.

Useful for futures-positioning context: rates (10Y/5Y/2Y), energy
(WTI / NatGas), metals (gold / silver / copper), equity index (ES /
NQ), FX (DXY / EUR / JPY / GBP), vol (VIX).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx

from api.data.cache import get_cached, put_cached

CFTC_TTL = timedelta(hours=12)
_BASE = "https://publicreporting.cftc.gov/resource"
# Legacy Futures-Only report -- wider history than the disaggregated view.
_DATASET = "jun7-fc8e"
_HEADERS = {"accept": "application/json", "user-agent": "ForteResearch/0.1"}

# Friendly aliases the analyst is more likely to type than the CFTC's
# market name. Maps to a substring matched (case-insensitive) against
# `market_and_exchange_names`.
MARKET_ALIASES: dict[str, str] = {
    "10y": "10-YEAR U.S. TREASURY NOTES",
    "5y": "5-YEAR U.S. TREASURY NOTES",
    "2y": "2-YEAR U.S. TREASURY NOTES",
    "ust_bond": "U.S. TREASURY BONDS",
    "wti": "WTI-PHYSICAL",
    "natgas": "NAT GAS",
    "gold": "GOLD",
    "silver": "SILVER",
    "copper": "COPPER",
    "spx": "E-MINI S&P 500",
    "nasdaq": "NASDAQ-100",
    "russell": "RUSSELL 2000",
    "vix": "VIX FUTURES",
    "dxy": "USD INDEX",
    "eur": "EURO FX",
    "jpy": "JAPANESE YEN",
    "gbp": "BRITISH POUND",
}


def latest_positions(market: str, *, weeks: int = 12) -> list[dict[str, Any]]:
    """Fetch the most recent N weeks of positioning for a market.

    `market` accepts either a friendly alias (e.g. '10y', 'gold') or any
    substring of the CFTC's `market_and_exchange_names` field (e.g.
    'EURO FX'). Returns rows newest-first."""
    name_filter = MARKET_ALIASES.get(market.lower(), market).upper()
    query = {"market": name_filter, "weeks": weeks}
    cached = get_cached("cftc_cot", query, CFTC_TTL)
    if cached is not None:
        return cached["rows"]  # type: ignore[no-any-return]

    where = f"upper(market_and_exchange_names) like '%{name_filter}%'"
    params = {
        "$where": where,
        "$order": "report_date_as_yyyy_mm_dd DESC",
        "$limit": max(1, min(weeks, 52)),
    }
    r = httpx.get(f"{_BASE}/{_DATASET}.json", params=params, headers=_HEADERS, timeout=20.0)
    r.raise_for_status()
    raw = r.json() if isinstance(r.json(), list) else []

    def _i(row: dict[str, Any], key: str) -> int | None:
        v = row.get(key)
        if v in (None, "", "null"):
            return None
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None

    out: list[dict[str, Any]] = []
    for row in raw:
        comm_long = _i(row, "comm_positions_long_all") or 0
        comm_short = _i(row, "comm_positions_short_all") or 0
        nc_long = _i(row, "noncomm_positions_long_all") or 0
        nc_short = _i(row, "noncomm_positions_short_all") or 0
        out.append({
            "market": row.get("market_and_exchange_names"),
            "report_date": row.get("report_date_as_yyyy_mm_dd"),
            "comm_long": comm_long,
            "comm_short": comm_short,
            "comm_net": comm_long - comm_short,
            "noncomm_long": nc_long,
            "noncomm_short": nc_short,
            "noncomm_net": nc_long - nc_short,
            "open_interest": _i(row, "open_interest_all"),
        })
    put_cached("cftc_cot", query, {"rows": out})
    return out
