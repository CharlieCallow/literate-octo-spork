"""Tool definitions agents can call. Wrap the data layer + chart helpers."""

from __future__ import annotations

import json
from datetime import date as _date
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx as httpx_exc
import pandas as pd

from api.agents.base import Tool
from api.data import (
    arxiv,
    cftc,
    coingecko,
    ctgov,
    defillama,
    edgar,
    eia,
    form4,
    fred,
    gdelt,
    github,
    hn,
    openfda,
    reddit,
    wikipedia,
    worldbank,
)
from api.data import yfinance as yf_data
from api.render import charts as chart_helpers

# ---------- FRED ----------

def fred_series_tool() -> Tool:
    def fn(series_id: str, start: str | None = None, end: str | None = None, max_points: int = 240, **_: Any) -> str:
        s = fred.get_series(series_id, start=start, end=end)
        if max_points and len(s) > max_points:
            s = s.iloc[-max_points:]
        return json.dumps({
            "series_id": series_id,
            "url": f"https://fred.stlouisfed.org/series/{quote(series_id, safe='')}",
            "title": f"FRED: {series_id}",
            "n": int(len(s)),
            "start": s.index[0].date().isoformat() if len(s) else None,
            "end": s.index[-1].date().isoformat() if len(s) else None,
            "latest": float(s.iloc[-1]) if len(s) else None,
            "first": float(s.iloc[0]) if len(s) else None,
            "tail": [{"date": d.date().isoformat(), "value": float(v)} for d, v in s.tail(12).items()],
        })

    return Tool(
        name="fred_series",
        description="Fetch a FRED macro time series. Returns latest value, first/last dates, and a tail of recent observations.",
        input_schema={
            "type": "object",
            "properties": {
                "series_id": {"type": "string", "description": "FRED series identifier, e.g. 'DGS10', 'CPIAUCSL', 'UNRATE'."},
                "start": {"type": "string", "description": "ISO start date (YYYY-MM-DD), optional."},
                "end": {"type": "string", "description": "ISO end date, optional."},
                "max_points": {"type": "integer", "default": 240},
            },
            "required": ["series_id"],
        },
        fn=fn,
    )


# ---------- yfinance ----------

def yfinance_history_tool() -> Tool:
    def fn(ticker: str, period: str = "1y", interval: str = "1d") -> str:
        df = yf_data.get_history(ticker, period=period, interval=interval)
        quote_url = f"https://finance.yahoo.com/quote/{quote(ticker, safe='^=-')}"
        if df.empty:
            return json.dumps({"ticker": ticker, "url": quote_url, "n": 0, "error": "no data returned"})
        close = df["Close"].dropna()
        return json.dumps({
            "ticker": ticker,
            "url": quote_url,
            "title": f"Yahoo Finance: {ticker}",
            "n": int(len(close)),
            "start": close.index[0].date().isoformat(),
            "end": close.index[-1].date().isoformat(),
            "first_close": float(close.iloc[0]),
            "last_close": float(close.iloc[-1]),
            "pct_change": float((close.iloc[-1] / close.iloc[0] - 1) * 100),
            "high": float(close.max()),
            "low": float(close.min()),
            "tail": [{"date": d.date().isoformat(), "close": float(v)} for d, v in close.tail(8).items()],
        })

    return Tool(
        name="yfinance_history",
        description="Fetch price history for a ticker (stocks, ETFs, FX, crypto, futures). Returns first/last close, pct change, range, and recent tail.",
        input_schema={
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "yfinance ticker, e.g. 'AAPL', 'SPY', '^VIX', 'BTC-USD', 'EURUSD=X', 'CL=F'."},
                "period": {"type": "string", "description": "Lookback window: '1mo', '3mo', '6mo', '1y', '2y', '5y', '10y', 'ytd', 'max'.", "default": "1y"},
                "interval": {"type": "string", "description": "'1d', '1wk', '1mo'.", "default": "1d"},
            },
            "required": ["ticker"],
        },
        fn=fn,
    )


# ---------- Wikipedia ----------

def wikipedia_tool() -> Tool:
    def fn(title: str) -> str:
        out = wikipedia.summary(title)
        if not out.get("extract"):
            return json.dumps({"title": title, "error": "no summary available"})
        return json.dumps(out)

    return Tool(
        name="wikipedia_summary",
        description="Fetch the lead-paragraph summary of a Wikipedia article. Useful for definitional and background context.",
        input_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Article title, e.g. 'High-bandwidth memory', 'Glucagon-like peptide-1'."},
            },
            "required": ["title"],
        },
        fn=fn,
    )


# ---------- SEC EDGAR ----------

def edgar_filings_tool() -> Tool:
    def fn(ticker: str, form: str | None = None, limit: int = 5) -> str:
        rows = edgar.recent_filings(ticker, form=form, limit=limit)
        return json.dumps({"ticker": ticker, "form": form, "n": len(rows), "filings": rows})

    return Tool(
        name="edgar_filings",
        description="List recent SEC filings for a ticker. Optionally filter by form ('10-K', '10-Q', '8-K'). Returns filing dates, accession numbers, and direct URLs.",
        input_schema={
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Stock ticker, e.g. 'AAPL', 'NVDA'."},
                "form": {"type": "string", "description": "Filter by form type, e.g. '10-K'. Omit to get all forms."},
                "limit": {"type": "integer", "default": 5},
            },
            "required": ["ticker"],
        },
        fn=fn,
    )


def form4_tool() -> Tool:
    def fn(ticker: str, limit: int = 15) -> str:
        rows = form4.recent_insider_filings(ticker, limit=limit)
        return json.dumps({"ticker": ticker, "n": len(rows), "filings": rows})

    return Tool(
        name="form4_insiders",
        description=(
            "Recent SEC Form 4 (insider transaction) filings for a US-"
            "listed ticker. Returns filer name, filed date, accession, "
            "and a URL. Useful as a leading equity signal -- "
            "concentrated insider buying / selling around earnings or "
            "pivots is often the tell."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "limit": {"type": "integer", "default": 15, "maximum": 50},
            },
            "required": ["ticker"],
        },
        fn=fn,
    )


def cftc_cot_tool() -> Tool:
    def fn(market: str, weeks: int = 8) -> str:
        rows = cftc.latest_positions(market, weeks=weeks)
        return json.dumps({"market": market, "n": len(rows), "weeks": rows})

    return Tool(
        name="cftc_cot",
        description=(
            "Weekly CFTC Commitments of Traders positioning. Pass a "
            "friendly alias ('10y', '5y', '2y', 'wti', 'natgas', "
            "'gold', 'silver', 'copper', 'spx', 'nasdaq', 'russell', "
            "'vix', 'dxy', 'eur', 'jpy', 'gbp') or a CFTC market "
            "substring. Returns commercial / non-commercial nets and "
            "open interest by week, newest first. Useful for futures-"
            "positioning context on macro themes."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "market": {"type": "string"},
                "weeks": {"type": "integer", "default": 8, "maximum": 52},
            },
            "required": ["market"],
        },
        fn=fn,
    )


def edgar_extract_tool() -> Tool:
    def fn(url: str, section: str | None = None, query: str | None = None) -> str:
        try:
            payload = edgar.extract_filing_text(url, section=section, query=query)
        except (ValueError, httpx_exc.HTTPError) as e:
            return json.dumps({"url": url, "error": str(e)})
        # Carry the source url so the citation extractor picks it up.
        payload.setdefault("title", f"SEC filing extract: {section or query or 'head'}")
        return json.dumps(payload)

    return Tool(
        name="edgar_extract",
        description=(
            "Fetch the text of a specific SEC filing and pull a named section "
            "or a keyword-matched slice. Pass the full filing URL from "
            "`edgar_filings`. `section` options: 'risk_factors', 'mdna' "
            "(management discussion), 'business', 'legal_proceedings', "
            "'outlook', 'guidance', 'controls'. Or pass `query` for a "
            "free-text paragraph match (case-insensitive). Returns the "
            "matched text -- quote it directly in your notes for primary-"
            "source citations."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Full SEC document URL (sec.gov/Archives/edgar/...).",
                },
                "section": {
                    "type": "string",
                    "enum": list(edgar._SECTION_ANCHORS.keys()),
                    "description": "Named section to slice from the filing.",
                },
                "query": {
                    "type": "string",
                    "description": "Free-text keyword query; returns paragraphs matching every term.",
                },
            },
            "required": ["url"],
        },
        fn=fn,
    )


# ---------- chart generator (FRED + yfinance) ----------

def make_chart_tool(out_dir: Path) -> Tool:
    def _frame(source: str, series_or_ticker: str, *, period: str,
               start: str | None, end: str | None) -> tuple[pd.DataFrame, str]:
        if source == "fred":
            s = fred.get_series(series_or_ticker, start=start, end=end)
            return pd.DataFrame({series_or_ticker: s}), "FRED"
        if source == "yfinance":
            df_full = yf_data.get_history(series_or_ticker, period=period)
            if df_full.empty:
                return pd.DataFrame(), "yfinance"
            close = df_full["Close"].dropna()
            return pd.DataFrame({series_or_ticker: close}), "yfinance"
        if source == "worldbank":
            # Format: 'COUNTRY:INDICATOR' (e.g. 'US:NY.GDP.MKTP.KD.ZG'). Annual.
            if ":" not in series_or_ticker:
                raise ValueError(
                    "worldbank source needs 'COUNTRY:INDICATOR' "
                    "(e.g. 'US:NY.GDP.MKTP.KD.ZG')."
                )
            country, indicator = series_or_ticker.split(":", 1)
            since = int(start[:4]) if start and len(start) >= 4 and start[:4].isdigit() else 2010
            rows = worldbank.get_series(country, indicator, since=since)
            if not rows:
                return pd.DataFrame(), "World Bank"
            # Worldbank returns descending; sort ascending so the line draws left-to-right.
            sorted_rows = sorted(rows, key=lambda r: r.get("year") or 0)
            idx = pd.to_datetime(
                [f"{r['year']}-12-31" for r in sorted_rows if r.get("year") is not None]
            )
            vals = [r["value"] for r in sorted_rows if r.get("year") is not None]
            return pd.DataFrame({series_or_ticker: vals}, index=idx), "World Bank"
        if source == "eia":
            # series_or_ticker is the EIA route (e.g. 'petroleum/pri/spt/data').
            try:
                rows = eia.get_series(series_or_ticker, frequency="monthly", limit=240)
            except RuntimeError as e:
                raise ValueError(str(e)) from e
            if not rows:
                return pd.DataFrame(), "EIA"
            # EIA periods can be 'YYYY', 'YYYY-MM', 'YYYY-MM-DD'. Sort + parse leniently.
            parsed: list[tuple[pd.Timestamp, float]] = []
            for r in rows:
                p = r.get("period")
                v = r.get("value")
                if p is None or v is None:
                    continue
                try:
                    ts = pd.to_datetime(str(p))
                    parsed.append((ts, float(v)))
                except (ValueError, TypeError):
                    continue
            if not parsed:
                return pd.DataFrame(), "EIA"
            parsed.sort(key=lambda x: x[0])
            idx = pd.DatetimeIndex([t for t, _ in parsed])
            label = series_or_ticker.strip("/").removesuffix("/data") or "EIA"
            return pd.DataFrame({label: [v for _, v in parsed]}, index=idx), "EIA"
        raise ValueError(
            f"Unknown source: {source}. Use 'fred', 'yfinance', 'worldbank', or 'eia'."
        )

    def fn(
        chart_kind: str,
        source: str,
        series_or_ticker: str,
        title: str,
        subtitle: str,
        filename: str,
        period: str = "1y",
        start: str | None = None,
        end: str | None = None,
        compare_with: str | None = None,
        events: list[dict[str, str]] | None = None,
        shaded: str | None = None,
        **_: Any,
    ) -> str:
        try:
            df, src_label = _frame(source, series_or_ticker, period=period, start=start, end=end)
        except ValueError as e:
            return str(e)
        if df.empty:
            return f"{source} returned no data for {series_or_ticker}"

        # Optional second series for comparison/regime/event. Use an inner join
        # so the chart starts at the later of the two inception dates -- prevents
        # a long tail of one series with no data for the other.
        if compare_with:
            df2, _ = _frame(source, compare_with, period=period, start=start, end=end)
            if not df2.empty:
                df = df.join(df2, how="inner").dropna()
                if df.empty:
                    return f"no overlapping data between {series_or_ticker} and {compare_with}"

        as_of = df.index[-1].date().isoformat() if len(df) else _date.today().isoformat()
        out_path = out_dir / filename

        try:
            if chart_kind == "line":
                chart_helpers.line_chart(df, title=title, subtitle=subtitle, source=src_label, as_of=as_of, out_path=out_path)
            elif chart_kind == "bar":
                first_col = df.columns[0]
                chart_helpers.bar_chart(df[first_col], title=title, subtitle=subtitle, source=src_label, as_of=as_of, out_path=out_path)
            elif chart_kind == "regime":
                chart_helpers.regime_chart(df, title=title, subtitle=subtitle, source=src_label, as_of=as_of, out_path=out_path, shaded=shaded or "nber")
            elif chart_kind == "comparison":
                chart_helpers.comparison_chart(df, title=title, subtitle=subtitle, source=src_label, as_of=as_of, out_path=out_path)
            elif chart_kind == "event":
                chart_helpers.event_chart(df, events or [], title=title, subtitle=subtitle, source=src_label, as_of=as_of, out_path=out_path)
            else:
                return f"Unknown chart_kind: {chart_kind}. Use line | bar | regime | comparison | event."
        except Exception as e:  # noqa: BLE001
            return f"chart render failed: {e}"

        return json.dumps({"path": str(out_path), "filename": filename, "as_of": as_of, "kind": chart_kind})

    return Tool(
        name="make_chart",
        description=(
            "Generate a chart in the Forte house style. Sources: 'fred' (US macro), "
            "'yfinance' (prices), 'worldbank' (cross-country macro, annual), 'eia' (US energy). "
            "Kinds: 'line' (default), 'bar', 'regime' (line + shaded recessions; pass shaded='nber' or skip), "
            "'comparison' (dual-axis when compare_with is set), 'event' (line + vertical event markers). "
            "Saves a PNG plus a sibling .json sidecar that the dashboard can render interactively."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "chart_kind": {"type": "string", "enum": ["line", "bar", "regime", "comparison", "event"]},
                "source": {"type": "string", "enum": ["fred", "yfinance", "worldbank", "eia"]},
                "series_or_ticker": {
                    "type": "string",
                    "description": (
                        "FRED series id (e.g. 'DGS10'), yfinance ticker (e.g. 'SPY'), "
                        "worldbank 'COUNTRY:INDICATOR' (e.g. 'US:NY.GDP.MKTP.KD.ZG'), "
                        "or EIA route (e.g. 'petroleum/pri/spt/data')."
                    ),
                },
                "title": {"type": "string"},
                "subtitle": {"type": "string"},
                "filename": {"type": "string", "description": "PNG filename, e.g. 'rates.png'."},
                "period": {"type": "string", "description": "yfinance lookback (default '1y'). Ignored for FRED.", "default": "1y"},
                "start": {"type": "string", "description": "FRED start date. Ignored for yfinance."},
                "end": {"type": "string", "description": "FRED end date. Ignored for yfinance."},
                "compare_with": {"type": "string", "description": "Optional second series/ticker for 'comparison' or to overlay on regime/event."},
                "events": {
                    "type": "array",
                    "description": "For chart_kind='event': vertical-line markers, each {date: 'YYYY-MM-DD', label: 'short label'}.",
                    "items": {"type": "object", "properties": {"date": {"type": "string"}, "label": {"type": "string"}}, "required": ["date", "label"]},
                },
                "shaded": {"type": "string", "description": "For chart_kind='regime': 'nber' for the bundled NBER recession bands."},
            },
            "required": ["chart_kind", "source", "series_or_ticker", "title", "subtitle", "filename"],
        },
        fn=fn,
    )


# ---------- Anthropic-managed web search (server-side) ----------

def reddit_hot_tool() -> Tool:
    def fn(subreddit: str, limit: int = 15) -> str:
        posts = reddit.get_hot(subreddit, limit=limit)
        return json.dumps({"subreddit": subreddit, "n": len(posts), "posts": posts})

    return Tool(
        name="reddit_hot",
        description="Fetch hot posts from a subreddit. Returns titles, scores, comments, permalinks. Useful for sentiment / what people are actually talking about.",
        input_schema={
            "type": "object",
            "properties": {
                "subreddit": {"type": "string", "description": "Subreddit name without /r/, e.g. 'wallstreetbets', 'stocks', 'investing', 'SecurityAnalysis'."},
                "limit": {"type": "integer", "default": 15, "description": "Max posts to return (1-25)."},
            },
            "required": ["subreddit"],
        },
        fn=fn,
    )


def hn_search_tool() -> Tool:
    def fn(query: str, tags: str = "story", hits: int = 15) -> str:
        stories = hn.search(query, tags=tags, hits=hits)
        return json.dumps({"query": query, "tags": tags, "n": len(stories), "stories": stories})

    return Tool(
        name="hn_search",
        description="Search Hacker News by keyword via the Algolia API. Useful for tech-adjacent themes (semis, AI, crypto, biotech, regulation).",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keywords."},
                "tags": {"type": "string", "default": "story", "description": "'story' (default), 'front_page', 'comment', 'show_hn', 'ask_hn'."},
                "hits": {"type": "integer", "default": 15},
            },
            "required": ["query"],
        },
        fn=fn,
    )


def web_search_tool(max_uses: int = 5) -> dict[str, Any]:
    """Anthropic-managed web search. Pass via Agent.run(server_tools=[web_search_tool()])."""
    return {"type": "web_search_20250305", "name": "web_search", "max_uses": max_uses}


# ---------- User-uploaded documents ----------

def uploaded_documents_tool(report_id: int) -> Tool:
    """Expose the user's uploaded research notes / spreadsheets to the analyst.
    Without args: returns the list of available documents with a short head-of-doc
    preview each. With `filename`: returns the full extracted text (already
    truncated at storage time). Slice via offset/limit for very long files."""

    from api import uploads as uploads_mod

    def fn(
        filename: str | None = None,
        offset: int = 0,
        limit: int = 30_000,
    ) -> str:
        docs = uploads_mod.list_documents(report_id)
        if filename is None:
            if not docs:
                return json.dumps({"documents": [], "n": 0})
            return json.dumps({
                "n": len(docs),
                "documents": [
                    {
                        "filename": d.filename,
                        "mime": d.mime,
                        "size_bytes": d.size_bytes,
                        "preview": d.summary,
                    }
                    for d in docs
                ],
            })
        for d in docs:
            if d.filename == filename:
                text = d.extracted_text or ""
                slice_ = text[offset : offset + max(0, limit)]
                return json.dumps({
                    "filename": d.filename,
                    "mime": d.mime,
                    "total_chars": len(text),
                    "offset": offset,
                    "returned_chars": len(slice_),
                    "text": slice_,
                })
        return json.dumps({"error": f"no upload named {filename}", "available": [d.filename for d in docs]})

    return Tool(
        name="uploaded_documents",
        description=(
            "Read research notes / CSVs / spreadsheets the user attached to "
            "this report. Call with no args to list available documents (each "
            "with a short preview). Pass `filename` to fetch full extracted "
            "plaintext for one. Use offset/limit to page through long files."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Document filename. Omit to list."},
                "offset": {"type": "integer", "default": 0},
                "limit": {"type": "integer", "default": 30000},
            },
        },
        fn=fn,
    )


# ---------- CoinGecko (crypto) ----------

def coingecko_markets_tool() -> Tool:
    def fn(limit: int = 25, vs_currency: str = "usd") -> str:
        coins = coingecko.market_overview(vs_currency=vs_currency, limit=limit)
        return json.dumps({"vs_currency": vs_currency, "n": len(coins), "coins": coins})

    return Tool(
        name="coingecko_markets",
        description="Top crypto coins by market cap with price + 24h/7d/30d % change + volume.",
        input_schema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 25, "maximum": 100},
                "vs_currency": {"type": "string", "default": "usd"},
            },
        },
        fn=fn,
    )


def coingecko_trending_tool() -> Tool:
    def fn() -> str:
        coins = coingecko.trending()
        return json.dumps({"trending": coins})

    return Tool(
        name="coingecko_trending",
        description="Top trending crypto coins on CoinGecko in the last 24h.",
        input_schema={"type": "object", "properties": {}},
        fn=fn,
    )


# ---------- EIA (US energy) ----------

def eia_series_tool() -> Tool:
    def fn(route: str, frequency: str = "monthly", limit: int = 60) -> str:
        try:
            rows = eia.get_series(route, frequency=frequency, limit=limit)
        except RuntimeError as e:
            return json.dumps({"error": str(e)})
        # EIA's data browser strips the trailing /data segment from API routes.
        browser_route = route.strip("/").removesuffix("/data")
        return json.dumps({
            "route": route,
            "url": f"https://www.eia.gov/opendata/browser/{browser_route}",
            "title": f"EIA: {browser_route}",
            "frequency": frequency,
            "n": len(rows),
            "rows": rows,
        })

    return Tool(
        name="eia_series",
        description=(
            "US Energy Information Administration time series. Routes look like "
            "'petroleum/pri/spt/data' (spot prices), 'electricity/retail-sales/data', "
            "'natural-gas/pri/sum/data', 'petroleum/stoc/wstk/data' (weekly stocks). "
            "Browse https://www.eia.gov/opendata/browser/ for ids."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "route": {"type": "string"},
                "frequency": {"type": "string", "enum": ["annual", "monthly", "weekly", "daily", "hourly"], "default": "monthly"},
                "limit": {"type": "integer", "default": 60},
            },
            "required": ["route"],
        },
        fn=fn,
    )


# ---------- ClinicalTrials.gov ----------

def clinical_trials_tool() -> Tool:
    def fn(query: str | None = None, sponsor: str | None = None,
           intervention: str | None = None, status: str | None = None,
           phase: str | None = None, limit: int = 15) -> str:
        rows = ctgov.search_studies(
            query=query, sponsor=sponsor, intervention=intervention,
            status=status, phase=phase, limit=limit,
        )
        return json.dumps({"n": len(rows), "studies": rows})

    return Tool(
        name="clinical_trials",
        description="Search ClinicalTrials.gov for biotech pipeline data: studies by drug, sponsor, phase, status. Returns NCT id + sponsor + phase + dates + url.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "sponsor": {"type": "string", "description": "Lead sponsor (e.g. 'Pfizer')."},
                "intervention": {"type": "string", "description": "Intervention name (e.g. 'semaglutide')."},
                "status": {"type": "string", "description": "Overall status filter (e.g. 'RECRUITING', 'COMPLETED')."},
                "phase": {"type": "string", "description": "'PHASE1' | 'PHASE2' | 'PHASE3' | 'PHASE4'."},
                "limit": {"type": "integer", "default": 15, "maximum": 50},
            },
        },
        fn=fn,
    )


# ---------- GitHub ----------

def github_repo_tool() -> Tool:
    def fn(owner: str, repo: str) -> str:
        return json.dumps(github.repo_summary(owner, repo))

    return Tool(
        name="github_repo",
        description="Summary metadata for a GitHub repo: stars, forks, language, topics, last push.",
        input_schema={
            "type": "object",
            "properties": {"owner": {"type": "string"}, "repo": {"type": "string"}},
            "required": ["owner", "repo"],
        },
        fn=fn,
    )


def github_search_tool() -> Tool:
    def fn(query: str, sort: str = "stars", limit: int = 10) -> str:
        repos = github.search_repos(query, sort=sort, limit=limit)
        return json.dumps({"query": query, "n": len(repos), "repos": repos})

    return Tool(
        name="github_search",
        description="Search GitHub repos by free-text query. Useful for tracking developer interest in a topic.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "sort": {"type": "string", "enum": ["stars", "forks", "updated", "help-wanted-issues"], "default": "stars"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
        fn=fn,
    )


# ---------- arXiv ----------

def arxiv_tool() -> Tool:
    def fn(query: str, limit: int = 10) -> str:
        papers = arxiv.search(query, limit=limit)
        return json.dumps({"query": query, "n": len(papers), "papers": papers})

    return Tool(
        name="arxiv_search",
        description="Search arXiv research papers by free-text query. Useful for AI / quant / biotech / physics theses.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 10, "maximum": 30},
            },
            "required": ["query"],
        },
        fn=fn,
    )


# ---------- GDELT ----------

def gdelt_tool() -> Tool:
    def fn(query: str, days: int = 3, limit: int = 15) -> str:
        articles = gdelt.search_articles(query, days=days, limit=limit)
        return json.dumps({"query": query, "n": len(articles), "articles": articles})

    return Tool(
        name="gdelt_news",
        description="Search global news via GDELT (broader and faster than web_search for global / geopolitical themes).",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "days": {"type": "integer", "default": 3, "minimum": 1, "maximum": 7},
                "limit": {"type": "integer", "default": 15},
            },
            "required": ["query"],
        },
        fn=fn,
    )


# ---------- World Bank ----------

def worldbank_tool() -> Tool:
    def fn(country: str, indicator: str, since: int = 2010) -> str:
        rows = worldbank.get_series(country, indicator, since=since)
        return json.dumps({
            "country": country, "indicator": indicator, "since": since,
            "url": (
                f"https://data.worldbank.org/indicator/{quote(indicator, safe='')}"
                f"?locations={quote(country.upper(), safe='')}"
            ),
            "title": f"World Bank: {indicator} ({country.upper()})",
            "n": len(rows), "rows": rows,
        })

    return Tool(
        name="worldbank_series",
        description=(
            "World Bank cross-country macro indicator. Pass an ISO country code "
            "(e.g. 'US', 'GBR', 'DEU') and an indicator id like "
            "'NY.GDP.MKTP.KD.ZG' (GDP growth %), 'FP.CPI.TOTL.ZG' (CPI YoY), "
            "'SL.UEM.TOTL.ZS' (unemployment %), 'GC.DOD.TOTL.GD.ZS' (debt/GDP). "
            "Browse https://data.worldbank.org/indicator for the full catalogue."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "country": {"type": "string", "description": "ISO-2 or ISO-3 code, e.g. 'US' or 'GBR'."},
                "indicator": {"type": "string", "description": "World Bank indicator id."},
                "since": {"type": "integer", "default": 2010, "description": "Earliest year."},
            },
            "required": ["country", "indicator"],
        },
        fn=fn,
    )


# ---------- openFDA ----------

def openfda_labels_tool() -> Tool:
    def fn(query: str, limit: int = 10) -> str:
        rows = openfda.drug_label_search(query, limit=limit)
        return json.dumps({"query": query, "n": len(rows), "labels": rows})

    return Tool(
        name="openfda_drug_labels",
        description="Search openFDA drug labels by drug name / sponsor / indication. Returns brand/generic name + manufacturer + indications.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "openFDA search syntax, e.g. 'openfda.brand_name:Ozempic'."},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
        fn=fn,
    )


def openfda_recalls_tool() -> Tool:
    def fn(query: str | None = None, limit: int = 10) -> str:
        rows = openfda.drug_recalls(query, limit=limit)
        return json.dumps({"n": len(rows), "recalls": rows})

    return Tool(
        name="openfda_recalls",
        description="Recent drug recalls from openFDA. Optional free-text filter.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
            },
        },
        fn=fn,
    )


# ---------- DeFiLlama ----------

def defillama_tool() -> Tool:
    def fn(scope: str = "protocols", limit: int = 25) -> str:
        if scope == "chains":
            rows = defillama.chain_tvl(limit=limit)
            return json.dumps({"scope": "chains", "n": len(rows), "rows": rows})
        rows = defillama.top_protocols(limit=limit)
        return json.dumps({"scope": "protocols", "n": len(rows), "rows": rows})

    return Tool(
        name="defillama",
        description="DeFi market structure: top protocols by TVL with 1d/7d change, or TVL by chain.",
        input_schema={
            "type": "object",
            "properties": {
                "scope": {"type": "string", "enum": ["protocols", "chains"], "default": "protocols"},
                "limit": {"type": "integer", "default": 25},
            },
        },
        fn=fn,
    )
