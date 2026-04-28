"""EIA adapter -- US Energy Information Administration time series.
Requires EIA_API_KEY (https://www.eia.gov/opendata/register.php)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx

from api.data.cache import get_cached, put_cached
from api.settings import settings

EIA_TTL = timedelta(hours=6)
_BASE = "https://api.eia.gov/v2"


def get_series(route: str, *, frequency: str = "monthly", limit: int = 60) -> list[dict[str, Any]]:
    """Fetch a series by route. Routes look like 'petroleum/pri/spt/data',
    'electricity/retail-sales/data', 'natural-gas/pri/sum/data', etc.
    See https://www.eia.gov/opendata/browser/ for route discovery."""
    if not settings.eia_api_key:
        raise RuntimeError("EIA_API_KEY not configured")

    query = {"route": route, "frequency": frequency, "limit": limit}
    cached = get_cached("eia", query, EIA_TTL)
    if cached is not None:
        return cached["rows"]  # type: ignore[no-any-return]

    url = f"{_BASE}/{route.lstrip('/')}"
    r = httpx.get(
        url,
        params={
            "api_key": settings.eia_api_key,
            "frequency": frequency,
            "data[]": "value",
            "sort[0][column]": "period",
            "sort[0][direction]": "desc",
            "offset": 0,
            "length": limit,
        },
        timeout=15.0,
    )
    r.raise_for_status()
    rows = r.json().get("response", {}).get("data", []) or []
    out = [{"period": row.get("period"), "value": row.get("value")} for row in rows]
    put_cached("eia", query, {"rows": out})
    return out
