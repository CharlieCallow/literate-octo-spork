"""Tests for the extend-the-surface + smaller-but-worthwhile features:

- pre-mortem prompt instruction
- forecast-tag instruction in analyst draft
- source-diversity computation + flagging on Report
- auto-thread agent prompt
- specialist registry keyword match (Jaccard)
- ask-the-analyst conversation primer
- position tracker helpers
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlmodel import Session, SQLModel, create_engine

from api.models import Call, CallDirection, CallOutcome, Persona, PersonaStatus, Report, ReportStage


# ---------- Pre-mortem & forecast tags (prompt instructions) ----------

def test_eic_edit_prompt_requires_pre_mortem() -> None:
    from api.agents.cost import CostTracker
    from api.agents.editor import EditorInChief

    cost = CostTracker(report_cap=1.0, day_cap=5.0)
    eic = EditorInChief(cost)
    captured = {}

    def fake_run(prompt: str, **kwargs):  # type: ignore[no-untyped-def]
        captured["prompt"] = prompt
        from api.agents.base import AgentResult
        return AgentResult(text="", cost_usd=0.0)

    with patch.object(eic, "run", side_effect=fake_run):
        eic.edit(brief="b", sections=[], chart_summary="(none)")

    p = captured["prompt"]
    # Falsification language now lives inside BEAR CASE (the previous split
    # between BEAR CASE and a separate CLOSING pre-mortem produced redundant
    # paragraphs at the end of the report; collapsed to a single block).
    assert "When we'll know we're wrong" in p
    assert "falsification trigger" in p


def test_analyst_draft_prompt_requires_forecast_horizons() -> None:
    from api.agents.analyst import Analyst
    from api.agents.base import AgentResult
    from api.agents.cost import CostTracker
    from api.settings import settings

    # Need a real persona file path; reuse an existing standing one.
    persona_path = settings.team_dir / "macro-strategist.md"
    if not persona_path.exists():
        pytest.skip("standing macro persona not on disk")

    cost = CostTracker(report_cap=1.0, day_cap=5.0)
    analyst = Analyst("macro-strategist.md", cost)

    captured = {}

    def fake_run(prompt: str, **kwargs):  # type: ignore[no-untyped-def]
        captured["prompt"] = prompt
        return AgentResult(text="", cost_usd=0.0)

    with patch.object(analyst, "run", side_effect=fake_run):
        analyst.draft("b", "n", "theme")

    p = captured["prompt"]
    assert "Forecast horizons" in p
    assert "horizon" in p.lower()
    assert "by Q3 2026" in p or "by year-end" in p


# ---------- Source diversity ----------

def test_domain_distribution_empty_returns_zero() -> None:
    from api.citations import domain_distribution
    top, share = domain_distribution([])
    assert top is None
    assert share == 0.0


def test_domain_distribution_dedupes_www() -> None:
    """www.ft.com and ft.com merge -- otherwise a Bloomberg-only report
    sneaks past with two dressed-up domains."""
    from api.citations import domain_distribution
    sources = [
        {"url": "https://www.ft.com/x"},
        {"url": "https://ft.com/y"},
        {"url": "https://reuters.com/z"},
    ]
    top, share = domain_distribution(sources)
    assert top == "ft.com"
    assert share == pytest.approx(2 / 3)


def test_domain_distribution_skips_missing_urls() -> None:
    from api.citations import domain_distribution
    sources = [{"url": "https://ft.com/a"}, {"url": None}, {"title": "no url"}]
    top, share = domain_distribution(sources)
    assert top == "ft.com"
    assert share == 1.0


# ---------- Auto-thread prompt ----------

def test_threader_prompt_includes_opening_and_house_view() -> None:
    from api.agents.cost import CostTracker
    from api.agents.threader import Threader

    cost = CostTracker(report_cap=1.0, day_cap=5.0)
    threader = Threader(cost)
    captured = {}

    def fake_run(prompt: str, **kwargs):  # type: ignore[no-untyped-def]
        captured["prompt"] = prompt
        from api.agents.base import AgentResult
        return AgentResult(text="1/...\n2/...\n3/...\n4/...\n5/...", cost_usd=0.0)

    with patch.object(threader, "run", side_effect=fake_run):
        threader.thread(
            theme="nuclear renaissance",
            opening="Markets keep telling you nuclear is back. Markets are right.",
            house_view_bottom="Buy the fuel cycle, sell the headline.",
        )

    p = captured["prompt"]
    assert "nuclear renaissance" in p
    assert "Markets keep telling" in p
    assert "Buy the fuel cycle" in p
    assert "5 tweets" in p


def test_threader_uses_haiku() -> None:
    from api.agents.cost import CostTracker
    from api.agents.threader import Threader
    from api.settings import settings

    cost = CostTracker(report_cap=1.0, day_cap=5.0)
    assert Threader(cost).model == settings.model_haiku


# ---------- Specialist registry ----------

@pytest.fixture
def reg_db(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """Isolated SQLite + a few seeded personas for registry tests."""
    eng = create_engine("sqlite://")
    SQLModel.metadata.create_all(eng)

    import api.db
    import api.specialist_registry
    monkeypatch.setattr(api.db, "engine", eng)
    monkeypatch.setattr(api.specialist_registry, "engine", eng)

    with Session(eng) as session:
        session.add(Persona(
            slug="biotech-clinical",
            status=PersonaStatus.archived,
            markdown="# Yelena Park — Biotech Clinical Trial Specialist\n\n**Role.** Reads phase 3 readouts, oncology pipelines, FDA risk.",
            name="Yelena Park",
            role="Biotech Clinical Trial Specialist",
        ))
        session.add(Persona(
            slug="semis-supply-chain",
            status=PersonaStatus.temp,
            markdown="# Tomas Lindqvist — Semis Supply Chain\n\n**Role.** TSMC, Samsung memory, capex cycles.",
            name="Tomas Lindqvist",
            role="Semiconductor Supply Chain",
        ))
        session.commit()
    return eng


def test_specialist_registry_matches_archived_biotech(reg_db) -> None:  # type: ignore[no-untyped-def]
    from api import specialist_registry
    hit = specialist_registry.find_match("Need a clinical trial specialist for biotech readouts")
    assert hit is not None
    assert hit.slug == "biotech-clinical"


def test_specialist_registry_matches_temp_semis(reg_db) -> None:  # type: ignore[no-untyped-def]
    from api import specialist_registry
    hit = specialist_registry.find_match("Semiconductor capex memory supply chain coverage")
    assert hit is not None
    assert hit.slug == "semis-supply-chain"


def test_specialist_registry_returns_none_for_unrelated(reg_db) -> None:  # type: ignore[no-untyped-def]
    from api import specialist_registry
    hit = specialist_registry.find_match("Brazilian sovereign debt restructuring expert")
    assert hit is None


def test_specialist_registry_skips_standing_personas(reg_db) -> None:  # type: ignore[no-untyped-def]
    """Standing-roster personas shouldn't be matched -- registry only looks
    at archived / temp specialists. Otherwise a brief asking for 'macro
    expert' would always match the standing macro analyst and skip the
    spawn entirely."""
    from api import specialist_registry
    # The standing pool is empty in this fixture; add one and verify it's
    # ignored.
    with Session(reg_db) as session:
        session.add(Persona(
            slug="macro-strategist",
            status=PersonaStatus.standing,
            markdown="# Henrik Voss — Macro\n\n**Role.** Macro coverage.",
            name="Henrik", role="Macro",
        ))
        session.commit()

    hit = specialist_registry.find_match("macro coverage Henrik")
    assert hit is None or hit.slug != "macro-strategist"


# ---------- Ask-the-analyst conversation ----------

def test_conversation_primer_includes_report_body() -> None:
    """The conversation agent's first turn must load the report so the
    persona can answer questions grounded in what the reader saw."""
    from api.agents.base import AgentResult
    from api.agents.conversation import Conversation
    from api.agents.cost import CostTracker
    from api.settings import settings

    persona_path = settings.team_dir / "macro-strategist.md"
    if not persona_path.exists():
        pytest.skip("standing macro persona not on disk")

    cost = CostTracker(report_cap=1.0, day_cap=5.0)
    conv = Conversation(persona_path=persona_path, cost=cost)
    captured = {}

    def fake_run(messages, *, max_tokens):  # type: ignore[no-untyped-def]
        captured["messages"] = messages
        return AgentResult(text="reply", cost_usd=0.0)

    with patch.object(conv, "_run_messages", side_effect=fake_run):
        conv.reply(
            report_theme="nuclear renaissance",
            report_markdown="The Western fuel cycle is short of conversion.",
            history=[],
            user_message="Why DXY and not EURUSD?",
        )

    msgs = captured["messages"]
    # First user turn = primer; assistant ack; final user = the question.
    assert msgs[0]["role"] == "user"
    assert "Western fuel cycle" in msgs[0]["content"]
    assert "nuclear renaissance" in msgs[0]["content"]
    assert msgs[1]["role"] == "assistant"
    assert msgs[-1]["role"] == "user"
    assert "DXY" in msgs[-1]["content"]


def test_conversation_includes_prior_history() -> None:
    """Multi-turn chat must carry the prior conversation forward, not start
    fresh on every reply."""
    from api.agents.base import AgentResult
    from api.agents.conversation import Conversation
    from api.agents.cost import CostTracker
    from api.settings import settings

    persona_path = settings.team_dir / "macro-strategist.md"
    if not persona_path.exists():
        pytest.skip("standing macro persona not on disk")

    cost = CostTracker(report_cap=1.0, day_cap=5.0)
    conv = Conversation(persona_path=persona_path, cost=cost)
    captured = {}

    def fake_run(messages, *, max_tokens):  # type: ignore[no-untyped-def]
        captured["messages"] = messages
        return AgentResult(text="ok", cost_usd=0.0)

    with patch.object(conv, "_run_messages", side_effect=fake_run):
        conv.reply(
            report_theme="t", report_markdown="body",
            history=[
                {"role": "user", "content": "first question"},
                {"role": "assistant", "content": "first answer"},
            ],
            user_message="follow-up",
        )

    contents = [m["content"] for m in captured["messages"]]
    assert "first question" in contents
    assert "first answer" in contents
    assert "follow-up" == captured["messages"][-1]["content"]


# ---------- Position tracker helpers ----------

@pytest.fixture
def calls_db(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    eng = create_engine("sqlite://")
    SQLModel.metadata.create_all(eng)
    import api.calls
    import api.db
    monkeypatch.setattr(api.db, "engine", eng)
    monkeypatch.setattr(api.calls, "engine", eng)
    return eng


def test_open_positions_returns_unmatured(calls_db) -> None:  # type: ignore[no-untyped-def]
    from api import calls as calls_mod
    with Session(calls_db) as session:
        r = Report(theme="t", stage=ReportStage.done)
        session.add(r); session.commit(); session.refresh(r)
        rid = r.id or 1

        # Not evaluated yet -- open.
        session.add(Call(
            report_id=rid, contributor_slug="x", asset="SPY",
            direction=CallDirection.long, horizon_days=90, price_at_call=100.0,
        ))
        # Already graded -- closed.
        session.add(Call(
            report_id=rid, contributor_slug="y", asset="QQQ",
            direction=CallDirection.long, horizon_days=90, price_at_call=100.0,
            evaluated_at=datetime.now(UTC), outcome=CallOutcome.hit,
            price_at_evaluation=110.0,
        ))
        session.commit()

    open_rows = calls_mod.open_positions()
    closed_rows = calls_mod.graded_history()
    assert len(open_rows) == 1 and open_rows[0].asset == "SPY"
    assert len(closed_rows) == 1 and closed_rows[0].asset == "QQQ"


def test_calls_for_report_orders_by_conviction(calls_db) -> None:  # type: ignore[no-untyped-def]
    from api import calls as calls_mod
    with Session(calls_db) as session:
        r = Report(theme="t", stage=ReportStage.done)
        session.add(r); session.commit(); session.refresh(r)
        rid = r.id or 1
        session.add(Call(report_id=rid, contributor_slug="x", asset="A",
                         direction=CallDirection.long, conviction=2, price_at_call=100))
        session.add(Call(report_id=rid, contributor_slug="x", asset="B",
                         direction=CallDirection.long, conviction=5, price_at_call=100))
        session.add(Call(report_id=rid, contributor_slug="x", asset="C",
                         direction=CallDirection.long, conviction=4, price_at_call=100))
        session.commit()

    rows = calls_mod.calls_for_report(rid)
    assert [r.asset for r in rows] == ["B", "C", "A"]


def test_has_calls_for_returns_false_when_empty(calls_db) -> None:  # type: ignore[no-untyped-def]
    from api import calls as calls_mod
    with Session(calls_db) as session:
        r = Report(theme="t", stage=ReportStage.done)
        session.add(r); session.commit(); session.refresh(r)
        rid = r.id or 1
    assert calls_mod.has_calls_for(rid) is False

    with Session(calls_db) as session:
        session.add(Call(report_id=rid, contributor_slug="x", asset="SPY",
                         direction=CallDirection.long, price_at_call=100.0))
        session.commit()
    assert calls_mod.has_calls_for(rid) is True


# ---------- Render-stage call extraction is idempotent ----------

def test_call_extraction_skipped_when_already_extracted(calls_db) -> None:  # type: ignore[no-untyped-def]
    """Re-running render after a fix must not duplicate Call rows."""
    from api import calls as calls_mod

    with Session(calls_db) as session:
        r = Report(theme="t", stage=ReportStage.done)
        session.add(r); session.commit(); session.refresh(r)
        rid = r.id or 1
        session.add(Call(report_id=rid, contributor_slug="x", asset="SPY",
                         direction=CallDirection.long, price_at_call=100.0))
        session.commit()

    # If has_calls_for is True we should skip re-extraction; this test
    # documents the contract the render stage relies on.
    assert calls_mod.has_calls_for(rid) is True

    rows_before = calls_mod.calls_for_report(rid)
    # Simulate the guard the render handler uses.
    if calls_mod.has_calls_for(rid):
        pass  # render skips extraction
    else:
        calls_mod.persist(rid, [{
            "asset": "QQQ", "direction": "long", "horizon_days": 90,
            "conviction": 3, "target_level": None,
            "contributor_slug": "x", "claim_text": "",
        }])
    rows_after = calls_mod.calls_for_report(rid)
    assert len(rows_before) == len(rows_after) == 1
