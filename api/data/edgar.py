"""SEC EDGAR adapter. Pulls company filings via the public JSON API."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx

from api.data.cache import get_cached, put_cached

FILINGS_TTL = timedelta(days=7)
_UA = "ForteResearch contact@example.com"  # SEC requires identifying UA
_TICKER_URL = "https://www.sec.gov/files/company_tickers.json"


def _ticker_to_cik(ticker: str) -> str | None:
    """Resolve a ticker (e.g. 'AAPL') to its 10-digit zero-padded CIK."""
    cached = get_cached("edgar_ticker_map", {"v": 1}, FILINGS_TTL)
    if cached is None:
        r = httpx.get(_TICKER_URL, headers={"user-agent": _UA}, timeout=15.0)
        r.raise_for_status()
        data = r.json()
        # data is a dict of "{idx}": {cik_str, ticker, title}
        cached = {row["ticker"].upper(): str(row["cik_str"]).zfill(10) for row in data.values()}
        put_cached("edgar_ticker_map", {"v": 1}, cached)
    return cached.get(ticker.upper())


def recent_filings(ticker: str, *, form: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
    """Return the most recent filings for a ticker.
    `form` filters to e.g. '10-K', '10-Q', '8-K' if supplied."""
    cik = _ticker_to_cik(ticker)
    if not cik:
        return []

    query = {"cik": cik, "form": form, "limit": limit}
    cached = get_cached("edgar_filings", query, FILINGS_TTL)
    if cached is not None:
        return cached["filings"]  # type: ignore[no-any-return]

    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    r = httpx.get(url, headers={"user-agent": _UA, "accept": "application/json"}, timeout=15.0)
    r.raise_for_status()
    data = r.json()
    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accs = recent.get("accessionNumber", [])
    dates = recent.get("filingDate", [])
    primary = recent.get("primaryDocument", [])

    rows: list[dict[str, Any]] = []
    for f, acc, date, doc in zip(forms, accs, dates, primary, strict=False):
        if form and f != form:
            continue
        clean_acc = acc.replace("-", "")
        rows.append({
            "form": f,
            "filed_at": date,
            "accession": acc,
            "url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{clean_acc}/{doc}",
        })
        if len(rows) >= limit:
            break

    put_cached("edgar_filings", query, {"filings": rows})
    return rows
