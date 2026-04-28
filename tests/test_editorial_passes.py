"""Tests for the editorial-bar features:
- conviction-tag stripping before render
- DISAGREEMENT / BEAR CASE block parsing & rendering
- redteam stage non-blocking failure path
- audit stage skip when ledger is empty

The agents themselves (RedTeam, Auditor) are exercised via the prompt
shape; we don't hit the Anthropic API in tests."""

from __future__ import annotations

import pytest

from api.models import ReportStage
from api.workflow.state_machine import (
    STAGE_ORDER,
    _strip_conviction_tags,
    parse_edited,
)


def test_conviction_tags_stripped_inline() -> None:
    md = "10Y is going higher {c4}, but the curve is the trade {c3}. EM is roadkill {c5}."
    assert _strip_conviction_tags(md) == "10Y is going higher, but the curve is the trade. EM is roadkill."


def test_low_conviction_tags_also_stripped() -> None:
    """Low tags get stripped too -- the EIC is supposed to cull weak claims,
    but if any leak through they shouldn't appear in the rendered prose."""
    md = "Maybe priced in {c1}. Possibly relevant {c2}."
    out = _strip_conviction_tags(md)
    assert "{c" not in out
    assert "Maybe priced in" in out
    assert "Possibly relevant" in out


def test_strip_handles_no_tags() -> None:
    md = "No conviction markers in this paragraph."
    assert _strip_conviction_tags(md) == md


def test_new_stages_in_order() -> None:
    """redteam slots between draft and edit; audit between edit and render."""
    i_draft = STAGE_ORDER.index(ReportStage.draft)
    i_redteam = STAGE_ORDER.index(ReportStage.redteam)
    i_edit = STAGE_ORDER.index(ReportStage.edit)
    i_audit = STAGE_ORDER.index(ReportStage.audit)
    i_render = STAGE_ORDER.index(ReportStage.render)
    assert i_draft < i_redteam < i_edit < i_audit < i_render


def test_disagreement_skipped_when_none() -> None:
    sample = """# OPENING
x

# HOUSE VIEW (TOP)
y

# REVISED SECTIONS
## h
**author:** A
**role:** R

body

# DISAGREEMENT
(none)

# BEAR CASE
(none)

# HOUSE VIEW (BOTTOM)
z

# CLOSING
end
"""
    parsed = parse_edited(sample)
    assert parsed["disagreement"] == ""
    assert parsed["bear_case"] == ""


def test_redteam_persona_file_exists() -> None:
    """The redteam stage references team/devils-advocate.md; if it's missing
    the agent will fail at instantiation. Fail loud here instead."""
    from api.settings import settings
    assert (settings.team_dir / "devils-advocate.md").exists()


def test_redteam_slug_excluded_from_roster() -> None:
    """Saoirse is an orchestrator -- she shouldn't appear in the EIC's
    contributor picker, otherwise the EIC could 'recruit' her onto reports."""
    from api.workflow.state_machine import _NON_ROSTER_SLUGS
    assert "devils-advocate" in _NON_ROSTER_SLUGS

    from api.personas import ORCHESTRATOR_SLUGS
    assert "devils-advocate" in ORCHESTRATOR_SLUGS


def test_tool_outputs_attached_to_agent_result() -> None:
    """Smoke test the AgentResult -> ToolOutput plumbing. The audit stage
    relies on tool_outputs being a real list on every result."""
    from api.agents.base import AgentResult, ToolOutput

    r = AgentResult(text="x", cost_usd=0.0)
    assert r.tool_outputs == []

    r.tool_outputs.append(ToolOutput(tool="fred_series", input={"series_id": "DGS10"}, output="{}"))
    assert r.tool_outputs[0].tool == "fred_series"


def test_append_tool_outputs_writes_jsonl(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The research stage appends one JSONL line per tool call to
    wd/tool-outputs.jsonl; the auditor reads that file directly."""
    import json as _json

    from api.agents.base import AgentResult, ToolOutput
    from api.workflow.state_machine import _append_tool_outputs

    result = AgentResult(
        text="notes",
        cost_usd=0.0,
        tool_outputs=[
            ToolOutput(tool="fred_series", input={"series_id": "DGS10"}, output='{"latest": 4.32}'),
            ToolOutput(tool="yfinance_history", input={"ticker": "SPY"}, output='{"last_close": 480.1}'),
        ],
    )
    _append_tool_outputs(tmp_path, "macro-strategist", result)

    lines = (tmp_path / "tool-outputs.jsonl").read_text().splitlines()
    assert len(lines) == 2
    parsed = [_json.loads(line) for line in lines]
    assert parsed[0]["agent"] == "macro-strategist"
    assert parsed[0]["tool"] == "fred_series"
    assert "DGS10" in str(parsed[0]["input"])
    assert parsed[1]["tool"] == "yfinance_history"


def test_redteam_critique_prompt_includes_brief_and_sections() -> None:
    """Spot-check the prompt the bear sees so future refactors don't
    silently drop the analyst drafts from her input."""
    from unittest.mock import MagicMock, patch

    from api.agents.base import AgentResult
    from api.agents.cost import CostTracker
    from api.agents.redteam import RedTeam

    cost = CostTracker(report_cap=1.0, day_cap=5.0)
    rt = RedTeam(cost)

    captured = {}

    def fake_run(prompt: str, **kwargs):  # type: ignore[no-untyped-def]
        captured["prompt"] = prompt
        return AgentResult(text="bear note", cost_usd=0.0)

    with patch.object(rt, "run", side_effect=fake_run):
        rt.critique(
            brief="THE BRIEF: nuclear renaissance",
            sections=[{"heading": "Macro", "body": "rates body", "author": "Henrik", "role": "Macro"}],
            chart_summary="rates.png",
        )

    assert "THE BRIEF" in captured["prompt"]
    assert "rates body" in captured["prompt"]
    assert "Henrik" in captured["prompt"]
    assert "WHERE THIS REPORT IS WRONG" in captured["prompt"]
    _ = MagicMock  # keep the import live in case future tests need it


def test_auditor_prompt_includes_ledger_and_edited() -> None:
    """The auditor must see both the prose and the JSONL ledger; if either
    is missing the audit is meaningless."""
    from unittest.mock import patch

    from api.agents.auditor import Auditor
    from api.agents.base import AgentResult
    from api.agents.cost import CostTracker

    cost = CostTracker(report_cap=1.0, day_cap=5.0)
    auditor = Auditor(cost)

    captured = {}

    def fake_run(prompt: str, **kwargs):  # type: ignore[no-untyped-def]
        captured["prompt"] = prompt
        return AgentResult(text="audited prose", cost_usd=0.0)

    with patch.object(auditor, "run", side_effect=fake_run):
        auditor.review(
            edited="The 10Y is at 4.32%.",
            tool_outputs_jsonl='{"tool":"fred_series","output":"{\\"latest\\":4.32}"}\n',
        )

    assert "10Y is at 4.32" in captured["prompt"]
    assert "fred_series" in captured["prompt"]
    assert "AUDIT NOTES" in captured["prompt"]


def test_auditor_uses_haiku_regardless_of_mode() -> None:
    """The audit is mechanical; it should always run on Haiku to keep the
    cost contained, even in deep mode where everything else is Sonnet/Opus."""
    from api.agents.auditor import Auditor
    from api.agents.cost import CostTracker
    from api.settings import settings

    cost = CostTracker(report_cap=1.0, day_cap=5.0)
    auditor = Auditor(cost)
    assert auditor.model == settings.model_haiku
