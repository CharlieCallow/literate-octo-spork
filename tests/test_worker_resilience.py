"""Tests for the worker resilience fixes:

- Per-call timeout on the Anthropic client.
- Tighter rate-limit retry ceiling so a single call can't wedge a report.
- Stuck-job watchdog: jobs left `running` past their lease get reclaimed
  through the same failure path as a real exception.

Worker / runner are exercised against an in-memory sqlite engine so we
can drive job state directly without spinning up the real worker."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

# Import models at module load so SQLModel.metadata is fully populated
# BEFORE the in-memory engine's create_all runs in the fixture below.
import api.models  # noqa: F401

# ----------------------------------------------------------------------
# Anthropic client construction
# ----------------------------------------------------------------------

def test_anthropic_client_has_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """A naked Anthropic() with no timeout falls back to the SDK's 10-min
    default. That's how a single hung call freezes a whole report. We pin
    a tighter timeout at construction so the call eventually raises."""
    captured: dict[str, object] = {}

    class FakeAnthropic:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)
            self.messages = None  # not used in this test

    import api.agents.base as base_mod
    monkeypatch.setattr(base_mod, "Anthropic", FakeAnthropic)

    from api.agents.base import Agent
    from api.agents.cost import CostTracker

    cost = CostTracker(report_cap=1.0, day_cap=5.0)
    Agent(
        persona_path=base_mod.Path(__file__),  # any file will do as a stub
        cost=cost,
    )
    assert "timeout" in captured
    # Don't pin the exact value -- tightness is the point. Just ensure it's
    # well below the SDK's 10-min default.
    assert isinstance(captured["timeout"], (int, float))
    assert 0 < float(captured["timeout"]) <= 300  # type: ignore[arg-type]


def test_rate_limit_backoff_ceiling_is_tight() -> None:
    """The previous ceiling (4 × 65s) could burn ~5 minutes inside a single
    call. Pin the new ceiling to keep the failure surface bounded."""
    from api.agents.base import Agent

    # Worst-case total wait should be well under 2 minutes -- past that
    # point we want the failure to bubble up to the runner's stage retry
    # so the report doesn't silently freeze.
    worst_case = (
        Agent._RATE_LIMIT_ATTEMPTS * Agent._RATE_LIMIT_WAIT_CEILING_S
    )
    assert worst_case <= 120, (
        f"rate-limit worst-case wait of {worst_case}s is too long -- a "
        "stuck call will look like a stuck report"
    )


# ----------------------------------------------------------------------
# Stuck-job watchdog
# ----------------------------------------------------------------------

@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """In-memory sqlite engine, swapped into every module that holds a
    reference to the real engine. Mirrors the fixture in
    test_close_the_loop.py so DB tests can drive jobs without polluting
    the dev DB."""
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)

    import api.db
    import api.workflow.runner as runner_mod
    monkeypatch.setattr(api.db, "engine", engine)
    monkeypatch.setattr(runner_mod, "engine", engine)
    yield engine


def _seed_report(engine, *, mode=None):  # type: ignore[no-untyped-def]
    from api.models import Report, ReportMode, ReportStage

    with Session(engine) as session:
        report = Report(
            theme="test-theme",
            stage=ReportStage.draft,
            mode=mode or ReportMode.standard,
        )
        session.add(report)
        session.commit()
        session.refresh(report)
        return report.id  # type: ignore[return-value]


def test_watchdog_reclaims_running_job_past_deadline(db) -> None:  # type: ignore[no-untyped-def]
    """A job left `running` past its mode-specific lease should get routed
    through the failure path so retry/backoff still apply. Without this,
    a worker that died mid-stage leaves the report unrecoverable."""
    from api.models import Job, ReportStage
    from api.workflow.runner import reclaim_stuck_jobs

    report_id = _seed_report(db)
    long_ago = datetime.now(UTC) - timedelta(hours=1)
    with Session(db) as session:
        job = Job(
            report_id=report_id,
            stage=ReportStage.draft,
            status="running",
            attempts=1,
            started_at=long_ago,
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        stuck_id = job.id

    reclaimed = reclaim_stuck_jobs()
    assert reclaimed == 1

    with Session(db) as session:
        stuck = session.get(Job, stuck_id)
        assert stuck is not None
        assert stuck.status == "failed"
        assert stuck.last_error and "deadline" in stuck.last_error
        # A retry should have been queued (within MAX_ATTEMPTS) so the new
        # worker actually picks it up. Same machinery as a real exception.
        retries = session.exec(
            select(Job).where(Job.report_id == report_id, Job.status == "pending")
        ).all()
        assert len(retries) == 1
        assert retries[0].stage == ReportStage.draft
        assert retries[0].attempts == 1  # carries forward


def test_watchdog_leaves_recently_started_job_alone(db) -> None:  # type: ignore[no-untyped-def]
    """A job that's been running for a few seconds is not stuck -- the
    watchdog must not snipe live work."""
    from api.models import Job, ReportStage
    from api.workflow.runner import reclaim_stuck_jobs

    report_id = _seed_report(db)
    with Session(db) as session:
        job = Job(
            report_id=report_id,
            stage=ReportStage.draft,
            status="running",
            attempts=1,
            started_at=datetime.now(UTC) - timedelta(seconds=30),
        )
        session.add(job)
        session.commit()

    assert reclaim_stuck_jobs() == 0
    with Session(db) as session:
        live = session.exec(select(Job).where(Job.report_id == report_id)).all()
        assert len(live) == 1
        assert live[0].status == "running"


def test_watchdog_uses_mode_specific_deadline(db) -> None:  # type: ignore[no-untyped-def]
    """Deep-mode reports get a longer lease than fast-mode -- a deep run
    legitimately takes 15-30 minutes per stage, fast caps out at 5."""
    from api.models import Job, ReportMode, ReportStage
    from api.workflow.runner import (
        STUCK_JOB_DEADLINE_S,
        reclaim_stuck_jobs,
    )

    fast_id = _seed_report(db, mode=ReportMode.fast)
    deep_id = _seed_report(db, mode=ReportMode.deep)

    # 10-minute-old jobs: past the fast lease, well within the deep lease.
    started = datetime.now(UTC) - timedelta(minutes=10)
    assert started + timedelta(seconds=STUCK_JOB_DEADLINE_S[ReportMode.fast]) < datetime.now(UTC)
    assert started + timedelta(seconds=STUCK_JOB_DEADLINE_S[ReportMode.deep]) > datetime.now(UTC)

    with Session(db) as session:
        for rid in (fast_id, deep_id):
            session.add(Job(
                report_id=rid,
                stage=ReportStage.draft,
                status="running",
                attempts=1,
                started_at=started,
            ))
        session.commit()

    assert reclaim_stuck_jobs() == 1  # only the fast one
    with Session(db) as session:
        statuses = {
            j.report_id: j.status
            for j in session.exec(
                select(Job).where(Job.status.in_(("running", "failed")))  # type: ignore[union-attr]
            ).all()
        }
    assert statuses[fast_id] == "failed"
    assert statuses[deep_id] == "running"


def test_watchdog_exhausts_retries_and_fails_report(db) -> None:  # type: ignore[no-untyped-def]
    """Once a job has burned through MAX_ATTEMPTS, the watchdog reclaim
    should mark the report failed -- not silently re-queue. Otherwise a
    persistently broken stage just rotates forever."""
    from api.models import Job, Report, ReportStage
    from api.workflow.runner import MAX_ATTEMPTS, reclaim_stuck_jobs

    report_id = _seed_report(db)
    long_ago = datetime.now(UTC) - timedelta(hours=1)
    with Session(db) as session:
        # attempts already at the cap -- the next failure should not retry.
        session.add(Job(
            report_id=report_id,
            stage=ReportStage.draft,
            status="running",
            attempts=MAX_ATTEMPTS,
            started_at=long_ago,
        ))
        session.commit()

    assert reclaim_stuck_jobs() == 1

    with Session(db) as session:
        report = session.get(Report, report_id)
        assert report is not None
        assert report.stage == ReportStage.failed
        # No new pending job queued.
        pendings = session.exec(
            select(Job).where(Job.report_id == report_id, Job.status == "pending")
        ).all()
        assert pendings == []
