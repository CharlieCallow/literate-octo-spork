"""SEC EDGAR adapter. Pulls company filings via the public JSON API."""

from __future__ import annotations

import html
import re
from datetime import timedelta
from typing import Any

import httpx

from api.data.cache import get_cached, put_cached

FILINGS_TTL = timedelta(days=7)
DOC_TTL = timedelta(days=30)  # primary documents are immutable once filed
_UA = "ForteResearch contact@example.com"  # SEC requires identifying UA
_TICKER_URL = "https://www.sec.gov/files/company_tickers.json"

# Common 10-K / 10-Q section labels. Maps a friendly key the agent passes in
# to the regex anchor we hunt for in the document. Filings vary wildly in
# capitalisation and item-numbering; the anchor patterns are deliberately
# loose. The match position is used to slice between consecutive headings.
_SECTION_ANCHORS: dict[str, re.Pattern[str]] = {
    "risk_factors": re.compile(r"(?im)^\s*(?:item\s*1a[\.\s]*)?risk\s*factors\b"),
    "mdna": re.compile(
        r"(?im)^\s*(?:item\s*[27][\.\s]*)?management['’]?s?\s+discussion\s+and\s+analysis\b"
    ),
    "business": re.compile(r"(?im)^\s*(?:item\s*1[\.\s]*)?business\b"),
    "legal_proceedings": re.compile(r"(?im)^\s*(?:item\s*[13][\.\s]*)?legal\s+proceedings\b"),
    "outlook": re.compile(r"(?im)\boutlook\b"),
    "guidance": re.compile(r"(?im)\bguidance\b"),
    "controls": re.compile(
        r"(?im)^\s*(?:item\s*[49][a\.\s]*)?controls\s+and\s+procedures\b"
    ),
}

# Bound the extracted slice. SEC filings can run to MB of HTML; the agent
# context can't take that and the analyst doesn't need it -- a few thousand
# characters of the right paragraphs is the win.
_MAX_QUOTE_CHARS = 8000
_MAX_DOC_CHARS = 1_500_000  # safety net on the raw download

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")
_BLANKLINE_RE = re.compile(r"\n\s*\n+")


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


def _strip_html(raw: str) -> str:
    """Crude tag-strip + entity-decode. SEC HTML is regular enough that this
    produces clean paragraph text without a full parser dep."""
    text = _TAG_RE.sub(" ", raw)
    text = html.unescape(text)
    # Collapse runs of spaces/tabs but preserve line structure for splitting.
    text = _WS_RE.sub(" ", text)
    text = _BLANKLINE_RE.sub("\n\n", text)
    return text.strip()


def _slice_section(text: str, anchor: re.Pattern[str]) -> str | None:
    """Return text from the first `anchor` match to the next plausible top-
    level heading. Filings rarely give us perfect headings, so the slice is
    bounded by length not by the next item match (which sometimes never comes
    in 10-Q / S-1 layouts)."""
    m = anchor.search(text)
    if not m:
        return None
    start = m.start()
    # Look for the next item heading after the current heading line.
    # Searching from m.end() avoids re-matching the current section's header.
    next_item = re.search(r"^\s*item\s+\d+\b", text[m.end():], re.I | re.M)
    end = (m.end() + next_item.start()) if next_item else (start + _MAX_QUOTE_CHARS)
    end = min(end, start + _MAX_QUOTE_CHARS)
    return text[start:end].strip()


def _query_paragraphs(text: str, query: str, *, limit: int = 6) -> list[str]:
    """Return paragraphs containing every whitespace-separated term in
    `query`, capped at `limit` matches. Plain substring match on lowercase."""
    needles = [t for t in query.lower().split() if t]
    if not needles:
        return []
    out: list[str] = []
    for para in text.split("\n\n"):
        plain = para.strip()
        if not plain:
            continue
        low = plain.lower()
        if all(n in low for n in needles):
            out.append(plain)
            if len(out) >= limit:
                break
    return out


def extract_filing_text(
    url: str, *, section: str | None = None, query: str | None = None,
) -> dict[str, Any]:
    """Fetch a filing document and extract a section, a keyword slice, or
    the head of the doc. Returns a dict the tool wrapper can serialise --
    `text` is always present, `section` / `query` echo the inputs, `n_chars`
    is the raw document length so the agent can decide whether to refine."""
    if not url.startswith("https://www.sec.gov/"):
        raise ValueError("EDGAR extract only accepts sec.gov URLs")

    cached = get_cached("edgar_doc", {"url": url}, DOC_TTL)
    if cached is not None:
        full_text = str(cached.get("text", ""))
    else:
        r = httpx.get(url, headers={"user-agent": _UA, "accept": "text/html"}, timeout=30.0)
        r.raise_for_status()
        raw = r.text[:_MAX_DOC_CHARS]
        full_text = _strip_html(raw)
        put_cached("edgar_doc", {"url": url}, {"text": full_text})

    out: dict[str, Any] = {"url": url, "n_chars": len(full_text)}

    if section:
        anchor = _SECTION_ANCHORS.get(section.lower())
        if anchor is None:
            out["error"] = (
                f"unknown section '{section}'. Valid: "
                + ", ".join(sorted(_SECTION_ANCHORS))
            )
            out["text"] = ""
            return out
        sliced = _slice_section(full_text, anchor)
        out["section"] = section
        out["text"] = (sliced or "")[:_MAX_QUOTE_CHARS]
        return out

    if query:
        paras = _query_paragraphs(full_text, query)
        joined = "\n\n".join(paras)
        out["query"] = query
        out["text"] = joined[:_MAX_QUOTE_CHARS]
        out["n_paragraphs"] = len(paras)
        return out

    # No section / query -- return the head of the doc so the agent can
    # decide where to dig.
    out["text"] = full_text[:_MAX_QUOTE_CHARS]
    return out
