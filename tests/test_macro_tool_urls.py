"""Macro tool wrappers must embed canonical source URLs.

The analyst persona has nothing else to cite for a quantitative claim --
without a deep link in the tool output, the model falls back to web_search
hits and ends up linking publisher homepages. The url field also flows
through `_extract_local_citations` into the Sources list automatically."""

from __future__ import annotations

import json
from typing import Any

import pytest


@pytest.fixture
def patched_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub out the data-layer fetches so we can exercise tool wrappers
    without hitting the network or needing real API keys."""
    import pandas as pd

    from api.data import eia, fred, worldbank
    from api.data import yfinance as yf_data

    s = pd.Series(
        [4.30, 4.32],
        index=pd.to_datetime(["2026-04-25", "2026-04-26"]),
        name="DGS10",
    )
    monkeypatch.setattr(fred, "get_series", lambda *a, **kw: s)

    df = pd.DataFrame(
        {"Close": [430.0, 435.0]},
        index=pd.to_datetime(["2026-04-25", "2026-04-26"]),
    )
    monkeypatch.setattr(yf_data, "get_history", lambda *a, **kw: df)

    monkeypatch.setattr(
        worldbank,
        "get_series",
        lambda *a, **kw: [{"year": 2024, "value": 2.7}],
    )
    monkeypatch.setattr(
        eia,
        "get_series",
        lambda *a, **kw: [{"period": "2026-03", "value": 78.4}],
    )


def _call(tool_factory: Any, **kwargs: Any) -> dict[str, Any]:
    return json.loads(tool_factory().fn(**kwargs))


def test_fred_tool_embeds_series_page_url(patched_data: None) -> None:
    from api.agents.tools import fred_series_tool

    out = _call(fred_series_tool, series_id="DGS10")
    assert out["url"] == "https://fred.stlouisfed.org/series/DGS10"
    assert "FRED" in out["title"]


def test_yfinance_tool_embeds_quote_page_url(patched_data: None) -> None:
    from api.agents.tools import yfinance_history_tool

    out = _call(yfinance_history_tool, ticker="SPY")
    assert out["url"] == "https://finance.yahoo.com/quote/SPY"
    assert "SPY" in out["title"]


def test_yfinance_tool_handles_special_chars(patched_data: None) -> None:
    from api.agents.tools import yfinance_history_tool

    # ^VIX, =F (futures), -USD (crypto pairs) should all stay readable.
    assert _call(yfinance_history_tool, ticker="^VIX")["url"] == "https://finance.yahoo.com/quote/^VIX"
    assert _call(yfinance_history_tool, ticker="CL=F")["url"] == "https://finance.yahoo.com/quote/CL=F"
    assert _call(yfinance_history_tool, ticker="BTC-USD")["url"] == "https://finance.yahoo.com/quote/BTC-USD"


def test_worldbank_tool_embeds_indicator_url(patched_data: None) -> None:
    from api.agents.tools import worldbank_tool

    out = _call(worldbank_tool, country="US", indicator="NY.GDP.MKTP.KD.ZG")
    assert out["url"] == "https://data.worldbank.org/indicator/NY.GDP.MKTP.KD.ZG?locations=US"


def test_eia_tool_embeds_browser_url(patched_data: None) -> None:
    from api.agents.tools import eia_series_tool

    out = _call(eia_series_tool, route="petroleum/pri/spt/data")
    # EIA's data browser strips the trailing /data segment.
    assert out["url"] == "https://www.eia.gov/opendata/browser/petroleum/pri/spt"


def test_macro_tool_url_is_picked_up_by_citation_extractor(patched_data: None) -> None:
    """End-to-end: the URL the tool emits flows through the citation
    extractor as a Citation object, so analysts no longer need to invent
    a URL to cite a FRED claim."""
    from api.agents.base import Citation, _extract_local_citations
    from api.agents.tools import fred_series_tool

    raw = fred_series_tool().fn(series_id="DGS10")
    cs: list[Citation] = []
    _extract_local_citations("fred_series", raw, cs)
    assert len(cs) == 1
    assert cs[0].url == "https://fred.stlouisfed.org/series/DGS10"
    assert cs[0].source == "fred_series"
