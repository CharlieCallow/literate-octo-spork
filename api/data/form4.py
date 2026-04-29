"""SEC Form 4 (insider transactions) adapter.

Uses EDGAR's full-text search backend for filings:
  https://efts.sec.gov/LATEST/search-index?q=&forms=4&ciks=<cik>

Returns recent Form 4 filings for a ticker -- filer name, accession,
filed date, and a URL to the primary doc. Free, no key. SEC requires
identifying user-agent (same string as the filings adapter).

Insider buying / selling at concentrated points in time (around earnings,
guidance changes, or pivots) is a sharp leading signal for equity work.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx

from api.data.cache import get_cached, put_cached
from api.data.edgar import _ticker_to_cik

FORM4_TTL = timedelta(hours=6)
_UA = "ForteResearch contact@example.com"
_SEARCH = "https://efts.sec.gov/LATEST/search-index"


def recent_insider_filings(ticker: str, *, limit: int = 15) -> list[dict[str, Any]]:
    """Return the N most recent Form 4 filings for a ticker. Each row has
    accession, filer (insider) name, filing date, and a URL to the
    primary doc (XML or rendered HTML, depending on what EDGAR
    publishes for that filing)."""
    cik = _ticker_to_cik(ticker)
    if not cik:
        return []
    query = {"cik": cik, "limit": limit}
    cached = get_cached("form4", query, FORM4_TTL)
    if cached is not None:
        return cached["rows"]  # type: ignore[no-any-return]

    params = {
        "q": "",
        "forms": "4",
        "ciks": cik,
        "hits": min(limit, 50),
    }
    r = httpx.get(
        _SEARCH,
        params=params,
        headers={"user-agent": _UA, "accept": "application/json"},
        timeout=20.0,
    )
    r.raise_for_status()
    payload = r.json() or {}
    hits = (payload.get("hits") or {}).get("hits") or []

    rows: list[dict[str, Any]] = []
    for h in hits[:limit]:
        src = h.get("_source") or {}
        acc = (h.get("_id") or "").split(":")[0]
        if not acc:
            continue
        # display_names is a list like ["Insider Name (CIK 0000123456)"].
        # Filter out the issuer entry (which shares the issuer CIK) so
        # the row reads as "who the insider is", not "who the company is".
        display_names = src.get("display_names") or []
        issuer_token = f"(CIK {int(cik):010d})"
        filer = next(
            (n for n in display_names if issuer_token not in n),
            display_names[0] if display_names else None,
        )
        rows.append({
            "accession": acc,
            "filed_at": src.get("file_date"),
            "filer": filer,
            "form": src.get("form"),
            "url": (
                f"https://www.sec.gov/cgi-bin/browse-edgar"
                f"?action=getcompany&CIK={cik}&type=4"
                "&dateb=&owner=include&count=40"
            ),
        })
    put_cached("form4", query, {"rows": rows})
    return rows
