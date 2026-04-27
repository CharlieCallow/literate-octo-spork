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
