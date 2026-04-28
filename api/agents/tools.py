"""Tool definitions agents can call. Wrap the data layer + chart helpers."""

from __future__ import annotations

import json
from datetime import date as _date
from pathlib import Path
from typing import Any

import pandas as pd

from api.agents.base import Tool
from api.data import edgar, fred, hn, reddit, wikipedia
from api.data import yfinance as yf_data
from api.render import charts as chart_helpers

# ---------- FRED ----------

def fred_series_tool() -> Tool:
    def fn(series_id: str, start: str | None = None, end: str | None = None, max_points: int = 240) -> str:
        s = fred.get_series(series_id, start=start, end=end)
        if max_points and len(s) > max_points:
            s = s.iloc[-max_points:]
        return json.dumps({
            "series_id": series_id,
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
        if df.empty:
            return json.dumps({"ticker": ticker, "n": 0, "error": "no data returned"})
        close = df["Close"].dropna()
        return json.dumps({
            "ticker": ticker,
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
        raise ValueError(f"Unknown source: {source}. Use 'fred' or 'yfinance'.")

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
    ) -> str:
        try:
            df, src_label = _frame(source, series_or_ticker, period=period, start=start, end=end)
        except ValueError as e:
            return str(e)
        if df.empty:
            return f"{source} returned no data for {series_or_ticker}"

        # Optional second series for comparison/regime/event.
        if compare_with:
            df2, _ = _frame(source, compare_with, period=period, start=start, end=end)
            if not df2.empty:
                df = df.join(df2, how="outer")

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
            "Generate a chart in the Forte house style. Backed by FRED (macro) or yfinance (prices). "
            "Kinds: 'line' (default), 'bar', 'regime' (line + shaded recessions; pass shaded='nber' or skip), "
            "'comparison' (dual-axis when compare_with is set), 'event' (line + vertical event markers). "
            "Saves a PNG plus a sibling .json sidecar that the dashboard can render interactively."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "chart_kind": {"type": "string", "enum": ["line", "bar", "regime", "comparison", "event"]},
                "source": {"type": "string", "enum": ["fred", "yfinance"]},
                "series_or_ticker": {"type": "string", "description": "FRED series id (e.g. 'DGS10') or yfinance ticker (e.g. 'SPY')."},
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
