"""Shared ticker -> issuer-info resolutions for a report.

The equity analyst pulls price history via yfinance during research, which
gives us an authoritative ticker -> issuer mapping for free. We persist that
map on `Report.tickers` and have downstream stages (the glossary builder, the
audit pass) read from it instead of running their own free-form lookup.

The bug this guards against: TLN resolves to "Talen Energy" on yfinance but
to "Talon Metals" on a generic name search, so a glossary built without the
analyst's resolutions can confidently cross-reference the wrong issuer in a
report whose central pair trade is long Talen.
"""

from __future__ import annotations

import logging
import re

from sqlmodel import Session

from api import calls as calls_mod
from api.data import yfinance as _yf
from api.db import engine
from api.models import Report

log = logging.getLogger(__name__)


def resolve_for_report(report_id: int) -> dict[str, dict[str, str]]:
    """Build the authoritative ticker -> issuer-info map for a report and
    persist it on `Report.tickers`. Reads the position table (calls) for the
    ticker universe and yfinance for the issuer info. Failures are recorded
    as an empty dict for that ticker so the audit stage can surface them."""
    resolutions: dict[str, dict[str, str]] = {}
    for c in calls_mod.calls_for_report(report_id):
        if not c.asset or c.asset in resolutions:
            continue
        info = _lookup(c.asset)
        resolutions[c.asset] = info
    with Session(engine) as session:
        r = session.get(Report, report_id)
        if r is not None:
            r.tickers = resolutions
            session.add(r)
            session.commit()
    return resolutions


def _lookup(ticker: str) -> dict[str, str]:
    """Pull issuer info from yfinance. Returns {} on any failure -- callers
    should treat an empty dict as a missing resolution."""
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
    except Exception:  # noqa: BLE001
        log.exception("yfinance info lookup failed for %s", ticker)
        # Fall back to the cached name-only path so we still get *something*
        # if .info() failed but a previous price-history call cached a name.
        try:
            name = _yf.get_ticker_name(ticker)
        except Exception:  # noqa: BLE001
            name = None
        return {"company_name": name} if name else {}

    name = info.get("longName") or info.get("shortName")
    if not name:
        return {}
    out: dict[str, str] = {"company_name": str(name).strip()}
    for src, dst in (
        ("exchange", "exchange"),
        ("sector", "sector"),
        ("industry", "industry"),
        ("country", "country"),
    ):
        v = info.get(src)
        if v:
            out[dst] = str(v).strip()
    return out


# A glossary "entry line" looks like: `**TERM** — definition.`
# Em-dash, en-dash and hyphen all show up in the wild.
_ENTRY_RE = re.compile(r"^\s*\*\*([^*]+)\*\*\s*[—–-]\s*(.+?)\s*$")


def filter_glossary_md(
    glossary_md: str,
    resolved: dict[str, dict[str, str]],
) -> str:
    """Strip / rewrite glossary lines whose term is a position-table ticker.

    Two rules:
    - If the term is a ticker present in `resolved`, rewrite the definition's
      issuer name to the authoritative `company_name`. Catches the case where
      the model emitted "TLN — Talon Metals, lithium miner..." for a report
      whose TLN is Talen Energy.
    - If the term is a ticker absent from `resolved` (and absent here means
      either nobody pulled price history for it, or the lookup failed), drop
      the entry entirely. The audit stage surfaces the gap separately.

    Non-ticker entries (HBM, TAM, drug names, etc.) pass through untouched.
    Ticker detection is conservative: only terms that match a key in
    `resolved`, or look like a bare equity ticker (1-5 uppercase letters,
    optional class suffix) AND are NOT a recognised non-ticker acronym,
    are subject to the rule. We err on the side of keeping the entry.
    """
    if not glossary_md.strip():
        return glossary_md
    resolved_upper = {k.upper(): v for k, v in resolved.items()}
    out_lines: list[str] = []
    for line in glossary_md.splitlines():
        m = _ENTRY_RE.match(line)
        if not m:
            out_lines.append(line)
            continue
        term, definition = m.group(1).strip(), m.group(2).strip()
        term_upper = term.upper()
        if term_upper in resolved_upper:
            info = resolved_upper[term_upper]
            authoritative = info.get("company_name")
            if authoritative:
                out_lines.append(_rewrite_entry(term, authoritative, definition, info))
            else:
                # Ticker is in the position table but the resolution failed.
                # Drop rather than ship a possibly-wrong definition.
                continue
        elif _looks_like_position_ticker(term, definition):
            # Ticker-shaped term that no analyst pulled. The model is
            # guessing; drop.
            continue
        else:
            out_lines.append(line)
    return "\n".join(out_lines)


def _rewrite_entry(
    term: str,
    company_name: str,
    original_definition: str,
    info: dict[str, str],
) -> str:
    """Rebuild a glossary line so the issuer name matches the resolution.

    If the original definition already starts with the authoritative name we
    keep it verbatim; otherwise replace the leading clause up to the first
    comma / em-dash / period with `<company_name>` and append the rest of the
    definition (which usually carries the analyst's actual gloss). Falls back
    to a minimal "<company_name> — <sector> issuer." if nothing usable
    remains."""
    leading = original_definition.split(",", 1)
    if leading[0].strip().lower().startswith(company_name.lower()):
        return f"**{term}** — {original_definition}"
    rest = leading[1].strip() if len(leading) > 1 else ""
    if rest:
        return f"**{term}** — {company_name}, {rest}"
    sector = info.get("sector") or info.get("industry")
    tail = f"{sector.lower()} issuer." if sector else "issuer."
    return f"**{term}** — {company_name}, {tail}"


_NON_TICKER_ACRONYMS = {
    "HBM", "TAM", "SAM", "SOM", "ASO", "ITC", "PJM", "MISO", "ERCOT",
    "CAISO", "FERC", "NRC", "SMR", "LNG", "OPEX", "CAPEX", "EBITDA",
    "ARR", "MRR", "GAAP", "NAV", "ROE", "ROIC", "WACC", "FCF", "DCF",
    "GDP", "CPI", "PCE", "ETF", "FOMC", "ECB", "BOE", "BOJ", "PBOC",
    "USD", "EUR", "JPY", "GBP", "CNY", "HY", "IG", "IPO", "M&A",
    "ESG", "AI", "ML", "API", "SDK", "OS", "RAM", "GPU", "CPU",
    "ASIC", "TPU", "TSMC",  # TSMC is a real ticker but its glossary name
                            # ("Taiwan Semiconductor") is unambiguous.
}


def _looks_like_position_ticker(term: str, definition: str) -> bool:
    """Heuristic: is this glossary term a single-stock equity ticker?

    True if the term is 1-5 uppercase letters (optional . / - class suffix)
    AND not a recognised general-finance acronym AND the definition reads
    like an issuer description (mentions a company-ish noun). Conservative
    by design -- the cost of a false positive is dropping a real glossary
    entry."""
    if not re.fullmatch(r"[A-Z]{1,5}([.-][A-Z]{1,2})?", term):
        return False
    if term in _NON_TICKER_ACRONYMS:
        return False
    issuer_signals = (
        "ticker", "issuer", "stock", "nyse", "nasdaq", "shares",
        "company", "corporation", "inc.", "corp.", "ltd", "plc",
    )
    low = definition.lower()
    return any(sig in low for sig in issuer_signals)


def audit_position_tickers(report_id: int) -> list[str]:
    """Return the list of position-table tickers that have no resolution.

    Read by the audit stage so a silent yfinance lookup failure surfaces in
    the dashboard rather than disappearing into an empty glossary entry."""
    with Session(engine) as session:
        r = session.get(Report, report_id)
        resolved = dict(r.tickers) if r and r.tickers else {}
    missing: list[str] = []
    for c in calls_mod.calls_for_report(report_id):
        if not c.asset:
            continue
        info = resolved.get(c.asset)
        if not info or not info.get("company_name"):
            if c.asset not in missing:
                missing.append(c.asset)
    return missing
