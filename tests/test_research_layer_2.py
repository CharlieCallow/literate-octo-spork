"""Tests for the second wave of research-pipeline improvements:
- Source diversity gate threaded into the EIC edit prompt
- Brief-coverage checker (Coverage agent + parsers)
- Pre-draft per-analyst self-audit (Auditor.pre_draft_audit)
- EDGAR quote extraction (data layer + tool)
- Cross-analyst rebuttal stage (new Analyst.rebut method + state machine)

Agents are exercised by patching .run() and asserting the constructed
prompt -- no Anthropic calls."""

from __future__ import annotations

from unittest.mock import patch

import pytest

# ----------------------------------------------------------------------
# Source-diversity gate
# ----------------------------------------------------------------------

def test_eic_edit_prompt_flags_source_concentration() -> None:
    """When >=40% of citations come from one publisher, the EIC's edit
    prompt must surface the imbalance so the editor demands wider sourcing."""
    from api.agents.base import AgentResult
    from api.agents.cost import CostTracker
    from api.agents.editor import EditorInChief

    cost = CostTracker(report_cap=1.0, day_cap=5.0)
    eic = EditorInChief(cost)
    captured: dict[str, str] = {}

    def fake_run(prompt: str, **kwargs):  # type: ignore[no-untyped-def]
        captured["prompt"] = prompt
        return AgentResult(text="edited", cost_usd=0.0)

    with patch.object(eic, "run", side_effect=fake_run):
        eic.edit(
            brief="b", sections=[], chart_summary="c",
            source_diversity={"top_domain": "ft.com", "top_share": 0.55},
        )

    assert "SOURCE-DIVERSITY FLAG" in captured["prompt"]
    assert "ft.com" in captured["prompt"]
    assert "55%" in captured["prompt"]


def test_eic_edit_prompt_omits_diversity_when_under_threshold() -> None:
    """Below the 40% concentration cutoff there's nothing to flag --
    don't pad the editor's prompt with non-actionable context."""
    from api.agents.base import AgentResult
    from api.agents.cost import CostTracker
    from api.agents.editor import EditorInChief

    cost = CostTracker(report_cap=1.0, day_cap=5.0)
    eic = EditorInChief(cost)
    captured: dict[str, str] = {}

    def fake_run(prompt: str, **kwargs):  # type: ignore[no-untyped-def]
        captured["prompt"] = prompt
        return AgentResult(text="edited", cost_usd=0.0)

    with patch.object(eic, "run", side_effect=fake_run):
        eic.edit(
            brief="b", sections=[], chart_summary="c",
            source_diversity={"top_domain": "ft.com", "top_share": 0.30},
        )

    assert "SOURCE-DIVERSITY FLAG" not in captured["prompt"]


# ----------------------------------------------------------------------
# Coverage check
# ----------------------------------------------------------------------

def test_coverage_uses_haiku() -> None:
    """The coverage check is mechanical matching, not analysis -- pin to
    Haiku regardless of report mode."""
    from api.agents.cost import CostTracker
    from api.agents.coverage import Coverage
    from api.settings import settings

    cost = CostTracker(report_cap=1.0, day_cap=5.0)
    c = Coverage(cost)
    assert c.model == settings.model_haiku


def test_coverage_parse_questions_from_brief() -> None:
    brief = """# ANGLE
something

# QUESTIONS
1. What's the bottleneck?
2. Who benefits?
3. What breaks the trade?

# CONTRIBUTORS
- `macro`: x
"""
    from api.agents.coverage import parse_questions

    qs = parse_questions(brief)
    assert qs == [
        (1, "What's the bottleneck?"),
        (2, "Who benefits?"),
        (3, "What breaks the trade?"),
    ]


def test_coverage_parse_status_bullets_and_extract_gaps() -> None:
    """The Coverage agent emits one bullet per question; the parser must
    extract status + anchor cleanly, and the gap helper should surface
    only the 'missing' rows back to the editor."""
    from api.agents.coverage import gaps, parse_coverage

    brief = """# QUESTIONS
1. Bottleneck?
2. Who benefits?
3. Bear case?
"""
    coverage_md = """- Q1: answered — Henrik on conversion supply
- Q2: partial — only one name discussed
- Q3: missing — no analyst returned to it
"""
    rows = parse_coverage(coverage_md)
    assert [r["status"] for r in rows] == ["answered", "partial", "missing"]
    assert rows[0]["n"] == "1"

    gap_list = gaps(brief, coverage_md)
    assert gap_list == ["Q3: Bear case?"]


def test_eic_edit_surfaces_coverage_gaps() -> None:
    """When research left a brief question unanswered, the editor's prompt
    must surface the gap so the closing doesn't promise an answer the body
    doesn't deliver."""
    from api.agents.base import AgentResult
    from api.agents.cost import CostTracker
    from api.agents.editor import EditorInChief

    cost = CostTracker(report_cap=1.0, day_cap=5.0)
    eic = EditorInChief(cost)
    captured: dict[str, str] = {}

    def fake_run(prompt: str, **kwargs):  # type: ignore[no-untyped-def]
        captured["prompt"] = prompt
        return AgentResult(text="edited", cost_usd=0.0)

    with patch.object(eic, "run", side_effect=fake_run):
        eic.edit(
            brief="b", sections=[], chart_summary="c",
            coverage_gaps=["Q3: What breaks the trade?"],
        )

    assert "UNANSWERED BRIEF QUESTIONS" in captured["prompt"]
    assert "What breaks the trade?" in captured["prompt"]


# ----------------------------------------------------------------------
# Pre-draft self-audit
# ----------------------------------------------------------------------

def test_pre_draft_audit_filters_ledger_to_caller_slug() -> None:
    """Each analyst's notes must be audited against THEIR tool calls, not
    a colleague's. Otherwise a number can get spuriously 'grounded' by
    data the analyst never saw."""
    from api.agents.auditor import Auditor
    from api.agents.base import AgentResult
    from api.agents.cost import CostTracker

    cost = CostTracker(report_cap=1.0, day_cap=5.0)
    auditor = Auditor(cost)
    captured: dict[str, str] = {}

    def fake_run(prompt: str, **kwargs):  # type: ignore[no-untyped-def]
        captured["prompt"] = prompt
        return AgentResult(text="audited notes", cost_usd=0.0)

    ledger = (
        '{"agent": "macro-strategist", "tool": "fred_series", "input": {"series_id": "DGS10"}, "output": "{\\"latest\\":4.32}"}\n'
        '{"agent": "equity-analyst", "tool": "yfinance_history", "input": {"ticker": "CCJ"}, "output": "{\\"last_close\\":35.4}"}\n'
    )

    with patch.object(auditor, "run", side_effect=fake_run):
        auditor.pre_draft_audit(
            agent_slug="macro-strategist",
            notes_md="Notes: 10Y at 4.32%.",
            tool_outputs_jsonl=ledger,
        )

    prompt = captured["prompt"]
    # The macro analyst's call must be in the prompt
    assert "DGS10" in prompt
    # The equity analyst's call must NOT appear -- only the calling
    # analyst's slice is forwarded.
    assert "CCJ" not in prompt
    # Hard pin on the conviction-tag preservation rule -- if it drifts the
    # post-audit notes will lose `{c4}` markers and the EIC's culling logic
    # breaks downstream.
    assert "{c1}" in prompt and "{c5}" in prompt


def test_pre_draft_audit_handles_no_tool_calls_for_agent() -> None:
    """An analyst can finish a research run with zero tool calls (web_search-
    only path). The audit prompt should still construct without crashing."""
    from api.agents.auditor import Auditor
    from api.agents.base import AgentResult
    from api.agents.cost import CostTracker

    cost = CostTracker(report_cap=1.0, day_cap=5.0)
    auditor = Auditor(cost)
    captured: dict[str, str] = {}

    def fake_run(prompt: str, **kwargs):  # type: ignore[no-untyped-def]
        captured["prompt"] = prompt
        return AgentResult(text="audited", cost_usd=0.0)

    with patch.object(auditor, "run", side_effect=fake_run):
        auditor.pre_draft_audit(
            agent_slug="scout-temp",
            notes_md="Some notes.",
            tool_outputs_jsonl="",
        )

    assert "no tool calls recorded" in captured["prompt"]


# ----------------------------------------------------------------------
# EDGAR extract
# ----------------------------------------------------------------------

def test_edgar_strip_html_collapses_tags_and_entities() -> None:
    from api.data.edgar import _strip_html

    raw = "<html><body><p>Risk Factors</p><p>The Company &amp; partners face <b>climate</b> risk.</p></body></html>"
    plain = _strip_html(raw)
    assert "Risk Factors" in plain
    assert "Company & partners face" in plain
    assert "<" not in plain and ">" not in plain


def test_edgar_extract_section_slices_at_anchor(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Pass a stubbed cache hit so we don't go to the network. Then ask
    for the 'risk_factors' section and confirm the slice starts at the
    heading, not the start of the doc."""
    from api.data import edgar

    body = (
        "Item 1. Business\n\nThe Company makes things.\n\n"
        "Item 1A. Risk Factors\n\nClimate risk could materially impact our operations.\n\n"
        "Item 2. Properties\n\nWe own buildings.\n"
    )
    # edgar.py does `from api.data.cache import get_cached, put_cached` so the
    # test patches the *imported* name in the edgar module, not the source.
    monkeypatch.setattr(
        edgar, "get_cached",
        lambda src, q, ttl: ({"text": body} if src == "edgar_doc" else None),
    )

    out = edgar.extract_filing_text(
        "https://www.sec.gov/Archives/edgar/data/000/foo/bar.htm",
        section="risk_factors",
    )
    assert out["section"] == "risk_factors"
    assert out["text"].startswith("Item 1A. Risk Factors") or "Risk Factors" in out["text"][:50]
    assert "Climate risk" in out["text"]
    assert "We own buildings" not in out["text"]


def test_edgar_extract_query_returns_matching_paragraphs(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from api.data import edgar

    body = (
        "Paragraph one talks about sales growth.\n\n"
        "Paragraph two mentions semaglutide volumes and pricing.\n\n"
        "Paragraph three is about taxes.\n\n"
        "Paragraph four returns to semaglutide and supply.\n"
    )
    monkeypatch.setattr(
        edgar, "get_cached",
        lambda src, q, ttl: ({"text": body} if src == "edgar_doc" else None),
    )

    out = edgar.extract_filing_text(
        "https://www.sec.gov/Archives/edgar/data/000/foo/bar.htm",
        query="semaglutide",
    )
    assert "semaglutide" in out["text"].lower()
    assert "taxes" not in out["text"].lower()
    assert out["n_paragraphs"] == 2


def test_edgar_extract_rejects_non_sec_urls() -> None:
    from api.data.edgar import extract_filing_text

    with pytest.raises(ValueError):
        extract_filing_text("https://evil.example.com/10k.htm")


def test_edgar_extract_tool_carries_url_through_for_citation(
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    """The local-citation extractor picks Citation objects out of any tool
    output that contains a `url` field. The edgar_extract tool must echo
    the filing URL back so quoted text gets credited correctly."""
    import json as _json

    from api.agents.tools import edgar_extract_tool
    from api.data import edgar

    monkeypatch.setattr(
        edgar, "get_cached",
        lambda src, q, ttl: (
            {"text": "Item 1A. Risk Factors\n\nClimate risk paragraph.\n"}
            if src == "edgar_doc" else None
        ),
    )
    url = "https://www.sec.gov/Archives/edgar/data/000/x/y.htm"
    out = _json.loads(edgar_extract_tool().fn(url=url, section="risk_factors"))
    assert out["url"] == url
    assert "Climate risk" in out["text"]


# ----------------------------------------------------------------------
# Cross-analyst rebuttal
# ----------------------------------------------------------------------

def test_rebuttal_stage_is_between_draft_and_redteam() -> None:
    from api.models import ReportStage
    from api.workflow.state_machine import STAGE_ORDER

    i_draft = STAGE_ORDER.index(ReportStage.draft)
    i_rebut = STAGE_ORDER.index(ReportStage.rebuttal)
    i_redteam = STAGE_ORDER.index(ReportStage.redteam)
    assert i_draft < i_rebut < i_redteam


def test_analyst_rebut_returns_empty_when_no_peers() -> None:
    """Solo-analyst reports skip the rebuttal call -- no spend, no prompt."""
    from api.agents.analyst import Analyst
    from api.agents.cost import CostTracker

    cost = CostTracker(report_cap=1.0, day_cap=5.0)
    analyst = Analyst("macro-strategist.md", cost)
    out = analyst.rebut(
        brief="b", my_section="my body", peer_sections=[], theme="t",
    )
    assert out.text == ""
    assert out.cost_usd == 0.0


def test_analyst_rebut_prompt_includes_peers() -> None:
    """The rebuttal call must surface peer authors + claims so the model
    can quote them; otherwise the disagreement is generic."""
    from api.agents.analyst import Analyst
    from api.agents.base import AgentResult
    from api.agents.cost import CostTracker

    cost = CostTracker(report_cap=1.0, day_cap=5.0)
    analyst = Analyst("macro-strategist.md", cost)

    captured: dict[str, str] = {}

    def fake_run(prompt: str, **kwargs):  # type: ignore[no-untyped-def]
        captured["prompt"] = prompt
        return AgentResult(text="i disagree because", cost_usd=0.0)

    with patch.object(analyst, "run", side_effect=fake_run):
        analyst.rebut(
            brief="brief",
            my_section="my section body",
            peer_sections=[
                {"author": "Henrik Vasseur", "role": "Macro", "body": "rates lower"},
                {"author": "Saoirse Mok", "role": "Equity", "body": "AAPL beneficiary"},
            ],
            theme="theme",
        )

    p = captured["prompt"]
    assert "Henrik Vasseur" in p
    assert "rates lower" in p
    assert "Saoirse Mok" in p
    assert "AAPL beneficiary" in p


def test_parse_rebuttals_extracts_blocks() -> None:
    from api.workflow.state_machine import _parse_rebuttals

    md = """# Rebuttals

## Henrik Vasseur — Macro

I'd push back on the equity framing — Saoirse has CCJ as the trade but the conversion bottleneck means the fuel-cycle names are the real beneficiary.

## Saoirse Mok — Equity

The macro framing under-rates Fed put optionality.
"""
    out = _parse_rebuttals(md)
    assert len(out) == 2
    assert out[0]["author"] == "Henrik Vasseur"
    assert out[0]["role"] == "Macro"
    assert "conversion bottleneck" in out[0]["body"]
    assert out[1]["author"] == "Saoirse Mok"
    assert "Fed put" in out[1]["body"]


def test_parse_rebuttals_drops_empty_bodies() -> None:
    """If an analyst produced no rebuttal text, the block has a heading but
    nothing under it -- those should be skipped, not flow through to the EIC
    as empty rebuttals."""
    from api.workflow.state_machine import _parse_rebuttals

    md = """## Henrik Vasseur — Macro

A real rebuttal here.

## Empty One — Analyst

"""
    out = _parse_rebuttals(md)
    assert len(out) == 1
    assert out[0]["author"] == "Henrik Vasseur"


def test_eic_edit_prompt_includes_rebuttals_when_supplied() -> None:
    from api.agents.base import AgentResult
    from api.agents.cost import CostTracker
    from api.agents.editor import EditorInChief

    cost = CostTracker(report_cap=1.0, day_cap=5.0)
    eic = EditorInChief(cost)
    captured: dict[str, str] = {}

    def fake_run(prompt: str, **kwargs):  # type: ignore[no-untyped-def]
        captured["prompt"] = prompt
        return AgentResult(text="edited", cost_usd=0.0)

    with patch.object(eic, "run", side_effect=fake_run):
        eic.edit(
            brief="b", sections=[], chart_summary="c",
            rebuttals=[{
                "author": "Henrik Vasseur", "role": "Macro",
                "body": "Conversion bottleneck not pounds.",
            }],
        )

    assert "CROSS-ANALYST REBUTTALS" in captured["prompt"]
    assert "Henrik Vasseur" in captured["prompt"]
    assert "Conversion bottleneck not pounds." in captured["prompt"]
