"""Tests for the calibration / glossary / cost-rollup / data-adapter
batch.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine

import api.models  # noqa: F401 -- registers tables before create_all


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)

    import api.calibration as cal_mod
    import api.db
    monkeypatch.setattr(api.db, "engine", engine)
    monkeypatch.setattr(cal_mod, "engine", engine)
    yield engine


def _seed_call(  # type: ignore[no-untyped-def]
    engine, *, slug, outcome=None, conviction=3, asset="SPY", report_id=None,
):
    from api.models import Call, CallDirection, CallOutcome, Report, ReportMode, ReportStage

    if report_id is None:
        with Session(engine) as session:
            r = Report(
                theme="t", stage=ReportStage.done,
                mode=ReportMode.standard, is_test=False,
            )
            session.add(r)
            session.commit()
            session.refresh(r)
            report_id = r.id

    with Session(engine) as session:
        c = Call(
            report_id=report_id, contributor_slug=slug,
            asset=asset, direction=CallDirection.long,
            conviction=conviction,
            outcome=CallOutcome(outcome) if outcome else None,
        )
        session.add(c)
        session.commit()


# ----------------------------------------------------------------------
# Calibration
# ----------------------------------------------------------------------

def test_calibration_for_persona_aggregates_outcomes(db) -> None:  # type: ignore[no-untyped-def]
    """Hit + partial + miss roll up correctly. Below the 3-graded
    threshold the hit_rate is None -- early data shouldn't masquerade
    as a track record."""
    from api.calibration import for_persona

    # Two hits, one miss -- 3 graded, threshold met. Plus one ungraded.
    _seed_call(db, slug="henrik", outcome="hit", conviction=4)
    _seed_call(db, slug="henrik", outcome="hit", conviction=4)
    _seed_call(db, slug="henrik", outcome="miss", conviction=5)
    _seed_call(db, slug="henrik", outcome=None, conviction=3)

    cal = for_persona("henrik")
    assert cal.n_total == 4
    assert cal.n_graded == 3
    assert cal.n_hit == 2
    assert cal.n_miss == 1
    assert cal.hit_rate == pytest.approx(2.0 / 3)
    assert cal.avg_conviction == pytest.approx((4 + 4 + 5 + 3) / 4)


def test_calibration_below_threshold_is_early(db) -> None:  # type: ignore[no-untyped-def]
    """1-2 graded calls -> hit_rate is None (the UI renders 'early')."""
    from api.calibration import for_persona

    _seed_call(db, slug="newhire", outcome="hit", conviction=3)
    _seed_call(db, slug="newhire", outcome=None, conviction=4)

    cal = for_persona("newhire")
    assert cal.n_total == 2
    assert cal.n_graded == 1
    assert cal.hit_rate is None  # below MIN_GRADED_FOR_RATE


def test_calibration_excludes_test_reports(db) -> None:  # type: ignore[no-untyped-def]
    """Calls extracted from test-mode reports must NOT influence the
    real calibration -- otherwise pipeline smoke runs would distort
    the analyst's track record."""
    from api.calibration import for_persona
    from api.models import Call, CallDirection, CallOutcome, Report, ReportMode, ReportStage

    with Session(db) as session:
        # Real report.
        real = Report(theme="real", stage=ReportStage.done, mode=ReportMode.standard, is_test=False)
        session.add(real)
        # Test-mode report.
        smoke = Report(theme="smoke", stage=ReportStage.done, mode=ReportMode.test, is_test=True)
        session.add(smoke)
        session.commit()
        session.refresh(real)
        session.refresh(smoke)

        session.add(Call(report_id=real.id, contributor_slug="x",
                         asset="SPY", direction=CallDirection.long,
                         conviction=4, outcome=CallOutcome.hit))
        session.add(Call(report_id=smoke.id, contributor_slug="x",
                         asset="SPY", direction=CallDirection.long,
                         conviction=5, outcome=CallOutcome.miss))
        session.commit()

    cal = for_persona("x")
    # Only the real report's call should count.
    assert cal.n_total == 1
    assert cal.n_hit == 1


def test_format_for_brief_omits_when_no_graded(db) -> None:  # type: ignore[no-untyped-def]
    """Brand-new persona with zero calls: brief must NOT include a fake
    hit-rate line. The function returns "" so the EIC's roster bullet
    stays plain."""
    from api.calibration import Calibration, format_for_brief

    cal = Calibration(slug="x", n_total=0, n_graded=0, n_hit=0,
                      n_partial=0, n_miss=0, hit_rate=None,
                      avg_conviction=None)
    assert format_for_brief(cal) == ""


def test_format_for_feedback_warns_on_overconfidence() -> None:
    """Calibration drift (high conviction, low hit rate) should be
    called out in the feedback log so the analyst sees the pattern."""
    from api.calibration import Calibration, format_for_feedback

    cal = Calibration(
        slug="x", n_total=10, n_graded=10,
        n_hit=3, n_partial=0, n_miss=7,
        hit_rate=0.30, avg_conviction=4.5,
    )
    out = format_for_feedback(cal)
    assert "Calibration drift" in out
    assert "high conviction, low hit rate" in out


# ----------------------------------------------------------------------
# Glossary
# ----------------------------------------------------------------------

def test_glossary_is_empty_handles_sentinel() -> None:
    """Model returns "(none)" when there are no terms worth defining;
    the renderer must NOT append an empty appendix in that case."""
    from api.agents.glossary import is_empty

    assert is_empty("")
    assert is_empty("(none)")
    assert is_empty("  (none)  \n")
    assert not is_empty("# Glossary\n\n**HBM** — high-bandwidth memory.")


# ----------------------------------------------------------------------
# Cost rollup
# ----------------------------------------------------------------------

def test_cost_rollup_buckets_audit_log_by_window(db) -> None:  # type: ignore[no-untyped-def]
    """Today / 7d / 30d totals are computed from audit_log, including
    rows from cancelled and failed reports (the per-report cost view
    misses those)."""
    from api.models import AuditLog
    from api.routes.reports import cost_rollup

    now = datetime.now(UTC)
    with Session(db) as session:
        # Today.
        session.add(AuditLog(actor="a", event="model_call",
                             cost_usd=0.10, created_at=now))
        # 3 days ago.
        session.add(AuditLog(actor="b", event="model_call",
                             cost_usd=0.20, created_at=now - timedelta(days=3)))
        # 14 days ago.
        session.add(AuditLog(actor="c", event="model_call",
                             cost_usd=0.50, created_at=now - timedelta(days=14)))
        # 60 days ago -- outside all windows.
        session.add(AuditLog(actor="d", event="model_call",
                             cost_usd=99.99, created_at=now - timedelta(days=60)))
        session.commit()

    with Session(db) as session:
        out = cost_rollup(session=session)
    assert out.today_usd == pytest.approx(0.10)
    assert out.last_7d_usd == pytest.approx(0.30)
    assert out.last_30d_usd == pytest.approx(0.80)


# ----------------------------------------------------------------------
# Data adapters
# ----------------------------------------------------------------------

def test_cftc_market_aliases_cover_macro_basics() -> None:
    """The aliases dict is the analyst-facing surface of the tool. A
    regression that drops a popular market silently is bad UX -- pin
    the most-likely typed shorthands."""
    from api.data.cftc import MARKET_ALIASES

    for key in ("10y", "wti", "gold", "spx", "vix", "dxy", "eur"):
        assert key in MARKET_ALIASES, f"alias `{key}` should be available"


def test_form4_returns_empty_for_unknown_ticker(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Bad ticker -> _ticker_to_cik returns None -> route returns []
    rather than blowing up."""
    import api.data.form4 as form4_mod

    monkeypatch.setattr(form4_mod, "_ticker_to_cik", lambda _t: None)
    rows = form4_mod.recent_insider_filings("NOPE")
    assert rows == []


def test_analyst_tool_kit_includes_new_data_sources() -> None:
    """A regression where someone reorders the imports and drops one of
    the new tools is easy. Pin that the analyst's tool list registers
    cftc_cot and form4_insiders by name."""
    from unittest.mock import patch

    from api.agents.analyst import Analyst
    from api.agents.cost import CostTracker

    cost = CostTracker(report_cap=1.0, day_cap=5.0)
    analyst = Analyst("macro-strategist.md", cost)

    captured: list[str] = []
    from api.agents.base import AgentResult

    def fake_run(_prompt, **kwargs):  # type: ignore[no-untyped-def]
        captured.extend(t.name for t in kwargs.get("tools", []))
        return AgentResult(text="", cost_usd=0.0)

    with patch.object(analyst, "run", side_effect=fake_run):
        from pathlib import Path

        from api.models import ReportMode
        analyst.research(
            brief="b", theme="t", working_dir=Path("/tmp"),
            mode=ReportMode.standard,
        )
    assert "cftc_cot" in captured
    assert "form4_insiders" in captured
