"""Tests for the close-the-firm-loop features:

- house view storage + brief integration
- performance ledger (call extraction, grading, hit-rate aggregation)
- theme tagger + Scout follow-up wiring
- voice stats + drift detection
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine

from api.models import Call, CallDirection, CallOutcome, HouseView, PersonaVoiceStat, Report, ReportStage


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """In-memory SQLite engine swapped into every module that imports it.

    Keeps each test isolated; lets us seed reports / calls / stats freely
    without touching the dev database."""
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)

    import api.calls
    import api.db
    import api.house_view
    import api.tagger
    import api.voice_stats
    monkeypatch.setattr(api.db, "engine", engine)
    monkeypatch.setattr(api.calls, "engine", engine)
    monkeypatch.setattr(api.house_view, "engine", engine)
    monkeypatch.setattr(api.tagger, "engine", engine)
    monkeypatch.setattr(api.voice_stats, "engine", engine)
    return engine


# ---------- House view ----------

def test_house_view_returns_seed_when_empty(db) -> None:  # type: ignore[no-untyped-def]
    from api import house_view
    text = house_view.get()
    assert "Forte House View" in text
    assert "(unset)" in text


def test_house_view_upsert_round_trip(db) -> None:  # type: ignore[no-untyped-def]
    from api import house_view
    house_view.upsert("# Forte House View\n\n## Rates path\nCutting next.\n", last_report_id=None)
    text = house_view.get()
    assert "Cutting next" in text
    # Update again -- single row.
    house_view.upsert("# Forte House View\n\n## Rates path\nHolding.\n", last_report_id=42)
    assert "Holding" in house_view.get()
    assert "Cutting" not in house_view.get()
    with Session(db) as session:
        rows = session.exec(SQLModel.metadata.tables["house_view"].select()).fetchall()
    assert len(rows) == 1


def test_brief_prompt_includes_house_view_when_set() -> None:
    """The EIC's brief prompt has to load the rolling view; otherwise the
    'reconcile or contradict' instruction is meaningless."""
    from unittest.mock import patch

    from api.agents.base import AgentResult
    from api.agents.cost import CostTracker
    from api.agents.editor import EditorInChief

    cost = CostTracker(report_cap=1.0, day_cap=5.0)
    eic = EditorInChief(cost)
    captured = {}

    def fake_run(prompt: str, **kwargs):  # type: ignore[no-untyped-def]
        captured["prompt"] = prompt
        return AgentResult(text="brief", cost_usd=0.0)

    with patch.object(eic, "run", side_effect=fake_run):
        eic.write_brief(
            "nuclear renaissance",
            available_contributors=[{"slug": "macro-strategist", "name": "Henrik", "role": "Macro"}],
            past_reports=[],
            house_view="## Rates path\nCutting next.\n## Equity stance\nLong industrials.",
        )

    assert "FIRM HOUSE VIEW" in captured["prompt"]
    assert "Cutting next" in captured["prompt"]
    assert "RECONCILE OR CONTRADICT" in captured["prompt"]


def test_brief_prompt_omits_house_view_when_empty() -> None:
    """When the rolling view is empty, don't pollute the prompt with a
    section headed FIRM HOUSE VIEW that contains nothing."""
    from unittest.mock import patch

    from api.agents.base import AgentResult
    from api.agents.cost import CostTracker
    from api.agents.editor import EditorInChief

    cost = CostTracker(report_cap=1.0, day_cap=5.0)
    eic = EditorInChief(cost)
    captured = {}

    def fake_run(prompt: str, **kwargs):  # type: ignore[no-untyped-def]
        captured["prompt"] = prompt
        return AgentResult(text="brief", cost_usd=0.0)

    with patch.object(eic, "run", side_effect=fake_run):
        eic.write_brief("first ever", available_contributors=[], past_reports=[], house_view="")

    assert "FIRM HOUSE VIEW" not in captured["prompt"]


# ---------- Calls / performance ledger ----------

def test_parse_extracted_drops_invalid_rows() -> None:
    from api.calls import parse_extracted

    raw = """{"calls": [
        {"asset": "SPY", "direction": "long", "horizon_days": 90, "conviction": 4,
         "contributor_slug": "macro-strategist", "claim": "indices grind higher"},
        {"asset": "CCJ", "direction": "INVALID", "horizon_days": 60,
         "contributor_slug": "equity-analyst"},
        {"direction": "long", "contributor_slug": "x"},
        "not a dict"
    ]}"""
    out = parse_extracted(raw)
    assert len(out) == 1
    assert out[0]["asset"] == "SPY"
    assert out[0]["direction"] == "long"


def test_parse_extracted_handles_code_fences() -> None:
    from api.calls import parse_extracted

    raw = """```json
{"calls": [{"asset": "BTC-USD", "direction": "long", "horizon_days": 30,
            "conviction": 5, "contributor_slug": "macro-strategist", "claim": "BTC > 100k"}]}
```"""
    out = parse_extracted(raw)
    assert len(out) == 1
    assert out[0]["asset"] == "BTC-USD"


def test_grade_long_hit_when_target_crossed() -> None:
    from api.calls import _grade_one
    out = _grade_one(direction=CallDirection.long, target=110.0, start=100.0, end=112.0)
    assert out == CallOutcome.hit


def test_grade_long_partial_when_halfway_to_target() -> None:
    from api.calls import _grade_one
    # Direction right, 60% of target move.
    out = _grade_one(direction=CallDirection.long, target=110.0, start=100.0, end=106.0)
    assert out == CallOutcome.partial


def test_grade_long_miss_when_wrong_direction() -> None:
    from api.calls import _grade_one
    out = _grade_one(direction=CallDirection.long, target=110.0, start=100.0, end=95.0)
    assert out == CallOutcome.miss


def test_grade_short_hit_when_price_falls() -> None:
    from api.calls import _grade_one
    # No target -- direction-only logic.
    out = _grade_one(direction=CallDirection.short, target=None, start=100.0, end=90.0)
    assert out == CallOutcome.hit


def test_grade_avoid_hit_when_flat_or_down() -> None:
    from api.calls import _grade_one
    assert _grade_one(direction=CallDirection.avoid, target=None, start=100.0, end=98.0) == CallOutcome.hit
    assert _grade_one(direction=CallDirection.avoid, target=None, start=100.0, end=110.0) == CallOutcome.miss


def test_hit_rate_by_persona_aggregates(db) -> None:  # type: ignore[no-untyped-def]
    from api import calls as calls_mod

    with Session(db) as session:
        # Seed a report so the FK is satisfied.
        r = Report(theme="t", stage=ReportStage.done)
        session.add(r); session.commit(); session.refresh(r)
        rid = r.id or 1
        session.add(Call(report_id=rid, contributor_slug="henrik", asset="SPY",
                         direction=CallDirection.long, outcome=CallOutcome.hit,
                         price_at_call=100.0, evaluated_at=datetime.now(UTC)))
        session.add(Call(report_id=rid, contributor_slug="henrik", asset="QQQ",
                         direction=CallDirection.long, outcome=CallOutcome.partial,
                         price_at_call=100.0, evaluated_at=datetime.now(UTC)))
        session.add(Call(report_id=rid, contributor_slug="henrik", asset="EEM",
                         direction=CallDirection.long, outcome=CallOutcome.miss,
                         price_at_call=100.0, evaluated_at=datetime.now(UTC)))
        session.add(Call(report_id=rid, contributor_slug="priya", asset="CCJ",
                         direction=CallDirection.long, outcome=CallOutcome.hit,
                         price_at_call=100.0, evaluated_at=datetime.now(UTC)))
        session.commit()

    rates = calls_mod.hit_rate_by_persona()
    assert rates["henrik"]["graded"] == 3
    # 1 hit + 0.5 partial + 0 miss = 1.5 / 3 = 0.5
    assert rates["henrik"]["hit_rate"] == pytest.approx(0.5)
    assert rates["priya"]["hit_rate"] == 1.0


def test_evaluate_due_skips_unmatured_calls(db, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    from api import calls as calls_mod

    monkeypatch.setattr(calls_mod, "_spot_price", lambda t: 110.0)

    with Session(db) as session:
        r = Report(theme="t", stage=ReportStage.done)
        session.add(r); session.commit(); session.refresh(r)
        rid = r.id or 1
        # Recent call (1 day ago, 90-day horizon) -- not due.
        session.add(Call(
            report_id=rid, contributor_slug="x", asset="SPY",
            direction=CallDirection.long, horizon_days=90,
            price_at_call=100.0,
            made_at=datetime.now(UTC) - timedelta(days=1),
        ))
        # Old call (200 days ago) -- due.
        session.add(Call(
            report_id=rid, contributor_slug="y", asset="SPY",
            direction=CallDirection.long, horizon_days=90,
            price_at_call=100.0,
            made_at=datetime.now(UTC) - timedelta(days=200),
        ))
        session.commit()

    n = calls_mod.evaluate_due()
    assert n == 1
    rates = calls_mod.hit_rate_by_persona()
    assert "y" in rates and rates["y"]["graded"] == 1
    assert "x" not in rates  # still unresolved


# ---------- Tagger / theme graph ----------

def test_tagger_parse_normalises_and_dedupes() -> None:
    from api.tagger import parse
    tickers, themes = parse("""```json
    {"tickers": ["spy", "SPY", "ccj"], "themes": ["AI capex", "ai capex", "Memory bandwidth"]}
    ```""")
    assert tickers == ["SPY", "CCJ"]
    # Themes are case-sensitive on the wire but deduped case-insensitively.
    assert "AI capex" in themes
    assert "Memory bandwidth" in themes
    assert len(themes) == 2


def test_tagger_recent_themes_pulls_from_done_reports(db) -> None:  # type: ignore[no-untyped-def]
    from api import tagger
    with Session(db) as session:
        r1 = Report(theme="A", stage=ReportStage.done, mentioned_themes=["AI capex", "TSMC"])
        r2 = Report(theme="B", stage=ReportStage.done, mentioned_themes=["AI capex", "memory bandwidth"])
        r3 = Report(theme="C", stage=ReportStage.failed, mentioned_themes=["nuclear"])
        session.add(r1); session.add(r2); session.add(r3); session.commit()

    out = tagger.recent_themes()
    # Done reports only; deduped; AI capex appears once.
    assert "AI capex" in out
    assert "memory bandwidth" in out or "TSMC" in out
    assert "nuclear" not in out  # failed report excluded


def test_scout_prompt_includes_recent_report_themes() -> None:
    from unittest.mock import patch

    from api.agents.base import AgentResult
    from api.agents.cost import CostTracker
    from api.agents.scout import Scout

    cost = CostTracker(report_cap=1.0, day_cap=5.0)
    scout = Scout(cost)
    captured = {}

    def fake_run(prompt: str, **kwargs):  # type: ignore[no-untyped-def]
        captured["prompt"] = prompt
        return AgentResult(text="", cost_usd=0.0)

    with patch.object(scout, "run", side_effect=fake_run):
        scout.run_daily_digest(
            recent_headlines=["yesterday's stuff"],
            recent_report_themes=["nuclear renaissance", "GLP-1 second-order effects"],
        )

    assert "FIRM'S RECENT WORK" in captured["prompt"]
    assert "nuclear renaissance" in captured["prompt"]
    assert "GLP-1 second-order effects" in captured["prompt"]
    assert "natural follow-up" in captured["prompt"].lower()


# ---------- Voice stats / drift detection ----------

def test_compute_voice_stats_basic() -> None:
    from api.voice_stats import compute
    text = (
        "The 10Y is too high. Maybe the curve inverts again. "
        "We are short rates -- with conviction {c4}. "
        "Powell could be wrong, but we doubt it."
    )
    m = compute(text)
    assert m.n_words > 0
    assert m.mean_sentence_words > 0
    # "maybe", "could be" -> 2 hedge hits across 4 sentences.
    assert m.hedge_ratio > 0
    assert m.emdash_per_1k > 0
    assert m.conviction_density > 0


def test_compute_voice_stats_empty() -> None:
    from api.voice_stats import compute
    m = compute("")
    assert m.n_words == 0
    assert m.mean_sentence_words == 0


def test_voice_stats_record_and_retrieve(db) -> None:  # type: ignore[no-untyped-def]
    from api import voice_stats
    with Session(db) as session:
        r = Report(theme="t", stage=ReportStage.done)
        session.add(r); session.commit(); session.refresh(r)
        rid = r.id or 1

    metrics = voice_stats.compute("Punchy. Sharp. No hedge. Trade-pounding.")
    voice_stats.record(rid, "henrik", metrics)
    with Session(db) as session:
        rows = session.exec(SQLModel.metadata.tables["persona_voice_stats"].select()).fetchall()
    assert len(rows) == 1


def test_drift_returns_empty_when_too_few_rows(db) -> None:  # type: ignore[no-untyped-def]
    from api import voice_stats
    assert voice_stats.detect_drift() == []


def test_drift_flags_persona_that_flattened(db) -> None:  # type: ignore[no-untyped-def]
    """Seed: 5 firm rows with low hedge_ratio + 5 baseline 'Henrik' rows
    with much higher hedge_ratio (his distinctive trait), then 5 recent
    Henrik rows that have flattened back to firm mean. Expect a drift
    alert on hedge_ratio."""
    import api.voice_stats as voice_stats

    monkey_setattr_patch_window = False
    if monkey_setattr_patch_window:
        pass

    with Session(db) as session:
        # Need a Report row for FK.
        r = Report(theme="t", stage=ReportStage.done)
        session.add(r); session.commit(); session.refresh(r)
        rid = r.id or 1

        # 10 firm-mean rows from another persona ("priya"): hedge_ratio = 0.1
        for i in range(10):
            session.add(PersonaVoiceStat(
                report_id=rid, persona_slug="priya",
                n_words=300, mean_sentence_words=12,
                hedge_ratio=0.1, emdash_per_1k=0,
                conviction_density=20,
                created_at=datetime.now(UTC) - timedelta(days=20 + i),
            ))

        # 5 baseline Henrik rows -- distinctive: hedge_ratio = 0.5
        for i in range(5):
            session.add(PersonaVoiceStat(
                report_id=rid, persona_slug="henrik",
                n_words=300, mean_sentence_words=20,
                hedge_ratio=0.5, emdash_per_1k=10,
                conviction_density=30,
                created_at=datetime.now(UTC) - timedelta(days=15 + i),
            ))

        # 5 recent Henrik rows -- flattened to firm mean: hedge_ratio ~= 0.2
        # (close to the firm trailing mean of ~0.22, well within DRIFT_PCT).
        for i in range(5):
            session.add(PersonaVoiceStat(
                report_id=rid, persona_slug="henrik",
                n_words=300, mean_sentence_words=12,
                hedge_ratio=0.2, emdash_per_1k=0,
                conviction_density=20,
                created_at=datetime.now(UTC) - timedelta(days=i),
            ))

        session.commit()

    alerts = voice_stats.detect_drift()
    henrik_alerts = [a for a in alerts if a.persona_slug == "henrik"]
    # We expect at least the hedge_ratio axis to have flagged.
    axes = {a.axis for a in henrik_alerts}
    assert "hedge_ratio" in axes
