"""Tests for the research-pipeline improvements:
- Pre-brief data primer (Haiku scout pass before EIC writes the brief)
- Sentiment tools (reddit_hot + hn_search) wired into the analyst toolset
- make_chart support for worldbank + eia sources

The agents themselves don't hit the Anthropic API in tests; we patch
.run() and inspect the prompt the agent constructs."""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

# ----------------------------------------------------------------------
# Pre-brief data primer
# ----------------------------------------------------------------------

def test_primer_uses_haiku() -> None:
    """The primer is a cheap reconnaissance pass; running it on Sonnet/Opus
    would defeat the cost trade. Pin to Haiku regardless of report mode."""
    from api.agents.cost import CostTracker
    from api.agents.primer import Primer
    from api.settings import settings

    cost = CostTracker(report_cap=1.0, day_cap=5.0)
    p = Primer(cost)
    assert p.model == settings.model_haiku


def test_primer_prompt_includes_theme_and_required_headings() -> None:
    """The output is parsed (or at least scanned) by the EIC -- if the
    headings drift, the brief stops anchoring to the primer's prints."""
    from api.agents.base import AgentResult
    from api.agents.cost import CostTracker
    from api.agents.primer import Primer

    cost = CostTracker(report_cap=1.0, day_cap=5.0)
    p = Primer(cost)
    captured: dict[str, str] = {}

    def fake_run(prompt: str, **kwargs):  # type: ignore[no-untyped-def]
        captured["prompt"] = prompt
        return AgentResult(text="primer memo", cost_usd=0.0)

    with patch.object(p, "run", side_effect=fake_run):
        p.primer("nuclear renaissance", subtitle="bottleneck not pounds")

    prompt = captured["prompt"]
    assert "nuclear renaissance" in prompt
    assert "bottleneck not pounds" in prompt
    assert "WHAT THE TAPE SAYS" in prompt
    assert "WHAT'S MOVING THIS WEEK" in prompt
    assert "OPEN QUESTIONS THE BRIEF SHOULD ADDRESS" in prompt


def test_eic_brief_prompt_threads_primer_when_supplied() -> None:
    """If a primer is passed, the EIC's prompt must include it -- otherwise
    the brief stage is paying for the primer call but ignoring its output."""
    from api.agents.base import AgentResult
    from api.agents.cost import CostTracker
    from api.agents.editor import EditorInChief

    cost = CostTracker(report_cap=1.0, day_cap=5.0)
    eic = EditorInChief(cost)
    captured: dict[str, str] = {}

    def fake_run(prompt: str, **kwargs):  # type: ignore[no-untyped-def]
        captured["prompt"] = prompt
        return AgentResult(text="brief", cost_usd=0.0)

    with patch.object(eic, "run", side_effect=fake_run):
        eic.write_brief(
            "nuclear renaissance",
            primer="# WHAT THE TAPE SAYS\n- CCJ +18% YTD, 35.40 last (Yahoo)",
        )

    assert "PRE-BRIEF DATA PRIMER" in captured["prompt"]
    assert "CCJ +18% YTD" in captured["prompt"]


def test_eic_brief_prompt_omits_primer_block_when_none() -> None:
    """When no primer is supplied (e.g. the primer call failed) the EIC's
    prompt should not contain the primer scaffold -- otherwise the model
    sees an empty section and may hallucinate prints to fill it."""
    from api.agents.base import AgentResult
    from api.agents.cost import CostTracker
    from api.agents.editor import EditorInChief

    cost = CostTracker(report_cap=1.0, day_cap=5.0)
    eic = EditorInChief(cost)
    captured: dict[str, str] = {}

    def fake_run(prompt: str, **kwargs):  # type: ignore[no-untyped-def]
        captured["prompt"] = prompt
        return AgentResult(text="brief", cost_usd=0.0)

    with patch.object(eic, "run", side_effect=fake_run):
        eic.write_brief("nuclear renaissance")

    assert "PRE-BRIEF DATA PRIMER" not in captured["prompt"]


# ----------------------------------------------------------------------
# Analyst sentiment tools
# ----------------------------------------------------------------------

def test_analyst_research_includes_sentiment_tools() -> None:
    """reddit_hot and hn_search must be in the analyst's tool list. They
    were Scout-only previously; thematic equity work needs them."""
    from api.agents.analyst import Analyst
    from api.agents.base import AgentResult
    from api.agents.cost import CostTracker

    cost = CostTracker(report_cap=1.0, day_cap=5.0)
    analyst = Analyst("macro-strategist.md", cost)

    captured: dict[str, list[str]] = {}

    def fake_run(prompt: str, *, tools=None, **kwargs):  # type: ignore[no-untyped-def]
        captured["tools"] = [t.name for t in (tools or [])]
        captured["prompt"] = prompt  # type: ignore[assignment]
        return AgentResult(text="notes", cost_usd=0.0)

    from pathlib import Path
    with patch.object(analyst, "run", side_effect=fake_run):
        analyst.research(
            brief="b", theme="t", working_dir=Path("/tmp/forte-test"),
        )

    assert "reddit_hot" in captured["tools"]
    assert "hn_search" in captured["tools"]


# ----------------------------------------------------------------------
# make_chart: worldbank + eia
# ----------------------------------------------------------------------

@pytest.fixture
def patched_chart_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the data-layer fetches the new chart sources rely on."""
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
        worldbank, "get_series",
        lambda country, indicator, **kw: [
            {"year": 2024, "value": 2.7},
            {"year": 2023, "value": 2.1},
            {"year": 2022, "value": 1.9},
        ],
    )
    monkeypatch.setattr(
        eia, "get_series",
        lambda route, **kw: [
            {"period": "2026-03", "value": 78.4},
            {"period": "2026-02", "value": 76.1},
            {"period": "2026-01", "value": 74.8},
        ],
    )


def test_make_chart_worldbank_frame_sorts_ascending_by_year(
    patched_chart_data: None, tmp_path,  # type: ignore[no-untyped-def]
) -> None:
    """worldbank API returns descending; the chart helper expects ascending
    so the line draws left-to-right. The _frame helper must reverse it."""
    import json as _json

    from api.agents.tools import make_chart_tool

    tool = make_chart_tool(tmp_path)
    out = tool.fn(
        chart_kind="line", source="worldbank",
        series_or_ticker="GBR:NY.GDP.MKTP.KD.ZG",
        title="UK GDP growth", subtitle="annual",
        filename="uk_gdp.png",
    )
    assert "uk_gdp.png" in out
    payload = _json.loads((tmp_path / "uk_gdp.json").read_text())
    years = [s[:4] for s in payload["index"]]
    assert years == sorted(years), f"index not ascending: {years}"
    assert years[0] == "2022" and years[-1] == "2024"


def test_make_chart_worldbank_rejects_missing_indicator(
    patched_chart_data: None, tmp_path,  # type: ignore[no-untyped-def]
) -> None:
    """worldbank needs 'COUNTRY:INDICATOR'. A bare slug should produce a
    helpful error message rather than a confusing matplotlib stack trace."""
    from api.agents.tools import make_chart_tool

    tool = make_chart_tool(tmp_path)
    out = tool.fn(
        chart_kind="line", source="worldbank",
        series_or_ticker="GBR",  # missing the ":INDICATOR" half
        title="x", subtitle="y", filename="bad.png",
    )
    assert "COUNTRY:INDICATOR" in out


def test_make_chart_eia_frame_renders(
    patched_chart_data: None, tmp_path,  # type: ignore[no-untyped-def]
) -> None:
    """EIA returns descending periods; the chart should render with them
    sorted ascending so the line direction matches FRED/yfinance behaviour."""
    import json as _json

    from api.agents.tools import make_chart_tool

    tool = make_chart_tool(tmp_path)
    out = tool.fn(
        chart_kind="line", source="eia",
        series_or_ticker="petroleum/pri/spt/data",
        title="WTI spot", subtitle="$/bbl, monthly",
        filename="wti.png",
    )
    assert "wti.png" in out
    payload = _json.loads((tmp_path / "wti.json").read_text())
    months = [s[:7] for s in payload["index"]]
    assert months == sorted(months), f"index not ascending: {months}"


def test_make_chart_unknown_source_lists_supported() -> None:
    """When the agent passes a typo'd source, the error should enumerate
    the four supported sources so the model self-corrects on retry."""
    from pathlib import Path

    from api.agents.tools import make_chart_tool

    tool = make_chart_tool(Path("/tmp/forte-test"))
    out = tool.fn(
        chart_kind="line", source="fed_h41",  # wrong
        series_or_ticker="x", title="x", subtitle="y", filename="x.png",
    )
    assert "fred" in out and "yfinance" in out and "worldbank" in out and "eia" in out
