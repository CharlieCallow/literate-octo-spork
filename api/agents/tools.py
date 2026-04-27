"""Tool definitions agents can call. Wrap the data layer + chart helpers."""

from __future__ import annotations

import json
from datetime import date as _date
from pathlib import Path
from typing import Any

import pandas as pd

from api.agents.base import Tool
from api.data import edgar, fred, wikipedia
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
    ) -> str:
        if source == "fred":
            s = fred.get_series(series_or_ticker, start=start, end=end)
            df = pd.DataFrame({series_or_ticker: s})
            src_label = "FRED"
        elif source == "yfinance":
            df_full = yf_data.get_history(series_or_ticker, period=period)
            if df_full.empty:
                return f"yfinance returned no data for {series_or_ticker}"
            close = df_full["Close"].dropna()
            df = pd.DataFrame({series_or_ticker: close})
            src_label = "yfinance"
        else:
            return f"Unknown source: {source}. Use 'fred' or 'yfinance'."

        as_of = df.index[-1].date().isoformat() if len(df) else _date.today().isoformat()
        out_path = out_dir / filename
        if chart_kind == "line":
            chart_helpers.line_chart(df, title=title, subtitle=subtitle, source=src_label, as_of=as_of, out_path=out_path)
        elif chart_kind == "bar":
            chart_helpers.bar_chart(df[series_or_ticker], title=title, subtitle=subtitle, source=src_label, as_of=as_of, out_path=out_path)
        else:
            return f"Unknown chart_kind: {chart_kind}"
        return json.dumps({"path": str(out_path), "filename": filename, "as_of": as_of})

    return Tool(
        name="make_chart",
        description="Generate a chart in the Forte house style. Backed by FRED (macro) or yfinance (prices). Saves a PNG.",
        input_schema={
            "type": "object",
            "properties": {
                "chart_kind": {"type": "string", "enum": ["line", "bar"]},
                "source": {"type": "string", "enum": ["fred", "yfinance"]},
                "series_or_ticker": {"type": "string", "description": "FRED series id (e.g. 'DGS10') or yfinance ticker (e.g. 'SPY')."},
                "title": {"type": "string"},
                "subtitle": {"type": "string"},
                "filename": {"type": "string", "description": "PNG filename, e.g. 'rates.png'."},
                "period": {"type": "string", "description": "yfinance lookback (default '1y'). Ignored for FRED.", "default": "1y"},
                "start": {"type": "string", "description": "FRED start date. Ignored for yfinance."},
                "end": {"type": "string", "description": "FRED end date. Ignored for yfinance."},
            },
            "required": ["chart_kind", "source", "series_or_ticker", "title", "subtitle", "filename"],
        },
        fn=fn,
    )


# ---------- Anthropic-managed web search (server-side) ----------

def web_search_tool(max_uses: int = 5) -> dict[str, Any]:
    """Anthropic-managed web search. Pass via Agent.run(server_tools=[web_search_tool()])."""
    return {"type": "web_search_20250305", "name": "web_search", "max_uses": max_uses}
