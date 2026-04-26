"""Tool definitions agents can call. Wrap the data layer + chart helpers."""

from __future__ import annotations

import json
from datetime import date as _date
from pathlib import Path
from typing import Any

import pandas as pd

from api.agents.base import Tool
from api.data import fred
from api.render import charts as chart_helpers


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


def make_chart_tool(out_dir: Path) -> Tool:
    def fn(
        chart_kind: str,
        series_id: str,
        title: str,
        subtitle: str,
        filename: str,
        start: str | None = None,
        end: str | None = None,
    ) -> str:
        s = fred.get_series(series_id, start=start, end=end)
        df = pd.DataFrame({series_id: s})
        as_of = s.index[-1].date().isoformat() if len(s) else _date.today().isoformat()
        out_path = out_dir / filename
        if chart_kind == "line":
            chart_helpers.line_chart(
                df, title=title, subtitle=subtitle, source="FRED",
                as_of=as_of, out_path=out_path,
            )
        elif chart_kind == "bar":
            chart_helpers.bar_chart(
                df[series_id], title=title, subtitle=subtitle, source="FRED",
                as_of=as_of, out_path=out_path,
            )
        else:
            return f"Unknown chart_kind: {chart_kind}"
        return json.dumps({"path": str(out_path), "filename": filename, "as_of": as_of})

    return Tool(
        name="make_chart",
        description="Generate a single FRED-backed chart in the Forte house style and save it as a PNG.",
        input_schema={
            "type": "object",
            "properties": {
                "chart_kind": {"type": "string", "enum": ["line", "bar"]},
                "series_id": {"type": "string"},
                "title": {"type": "string"},
                "subtitle": {"type": "string"},
                "filename": {"type": "string", "description": "PNG filename, e.g. 'rates.png'."},
                "start": {"type": "string"},
                "end": {"type": "string"},
            },
            "required": ["chart_kind", "series_id", "title", "subtitle", "filename"],
        },
        fn=fn,
    )


def web_search_tool() -> dict[str, Any]:
    """Anthropic-managed web search tool. Returned as-is to the API."""
    return {"type": "web_search_20250305", "name": "web_search", "max_uses": 5}
