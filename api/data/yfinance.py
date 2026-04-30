"""yfinance adapter. Equities, ETFs, FX, commodity futures, crypto."""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

from api.data.cache import get_cached, put_cached

PRICE_TTL = timedelta(hours=1)


def get_history(
    ticker: str,
    *,
    period: str = "1y",
    interval: str = "1d",
) -> pd.DataFrame:
    """Fetch a yfinance OHLCV history. `period` like '1mo', '6mo', '1y', '5y', 'max'."""
    query = {"ticker": ticker, "period": period, "interval": interval}
    cached = get_cached("yfinance", query, PRICE_TTL)
    if cached is not None:
        df = pd.DataFrame(cached["rows"])
        if not df.empty:
            df.index = pd.to_datetime(cached["index"])
        return df

    df = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=True)
    if df.empty:
        return df

    payload = {
        "index": [d.isoformat() for d in df.index.to_pydatetime()],
        "rows": df.reset_index(drop=True).to_dict(orient="list"),
        "fetched_at": datetime.utcnow().isoformat(),
    }
    put_cached("yfinance", query, payload)
    return df


# Company-name lookups don't move minute-to-minute; cache for a week so the
# glossary builder reads the same resolution as the price-history calls.
_TICKER_NAME_TTL = timedelta(days=7)


def get_ticker_name(ticker: str) -> str | None:
    """Resolve a yfinance ticker to its issuer name.

    Used by the glossary builder so a ticker that collides with an unrelated
    company name (TLN -> "Talon Metals" vs. the actual Talen Energy) gets the
    same answer the equity analyst's price-history call did. Returns None on
    any failure; the glossary then skips that entry rather than guessing."""
    query = {"ticker": ticker, "kind": "name"}
    cached = get_cached("yfinance", query, _TICKER_NAME_TTL)
    if cached is not None:
        name = cached.get("name")
        return str(name) if name else None
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception:  # noqa: BLE001
        return None
    name = info.get("longName") or info.get("shortName")
    if not name:
        return None
    name_str = str(name).strip()
    put_cached("yfinance", query, {"name": name_str})
    return name_str
