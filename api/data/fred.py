"""FRED adapter. Free-tier macro data from the St. Louis Fed."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
from fredapi import Fred

from api.data.cache import get_cached, put_cached
from api.settings import settings

MACRO_TTL = timedelta(days=1)


def _client() -> Fred:
    if not settings.fred_api_key:
        raise RuntimeError("FRED_API_KEY not configured")
    return Fred(api_key=settings.fred_api_key)


def get_series(
    series_id: str,
    *,
    start: date | str | None = None,
    end: date | str | None = None,
) -> pd.Series:
    """Fetch a FRED series and cache the response. Returns a pandas Series."""
    query = {"series_id": series_id, "start": str(start) if start else None, "end": str(end) if end else None}
    cached = get_cached("fred", query, MACRO_TTL)
    if cached is not None:
        s = pd.Series(cached["values"], index=pd.to_datetime(cached["index"]), name=series_id)
        return s

    fred = _client()
    s = fred.get_series(series_id, observation_start=start, observation_end=end)
    s = s.dropna()
    s.name = series_id
    payload = {
        "index": [d.isoformat() for d in s.index.to_pydatetime()],
        "values": [float(v) for v in s.values],
        "fetched_at": datetime.utcnow().isoformat(),
    }
    put_cached("fred", query, payload)
    return s
