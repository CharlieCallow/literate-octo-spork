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


# ----------------------------------------------------------------------
# Force-fail endpoint
# ----------------------------------------------------------------------

def test_force_fail_marks_running_and_pending_jobs_failed(db) -> None:  # type: ignore[no-untyped-def]
    """force_fail is the triage button for a stuck report -- it must
    actually clean up the running job (the cancel endpoint doesn't) so
    the worker stops chewing on a hung call."""
    from fastapi import HTTPException

    from api.models import Job, Report, ReportStage
    from api.routes.reports import force_fail

    report_id = _seed_report(db)
    with Session(db) as session:
        session.add(Job(
            report_id=report_id, stage=ReportStage.draft,
            status="running", attempts=1,
            started_at=datetime.now(UTC),
        ))
        session.add(Job(
            report_id=report_id, stage=ReportStage.redteam,
            status="pending", attempts=0,
        ))
        session.commit()

    with Session(db) as session:
        out = force_fail(report_id, session=session)
    assert out.stage == "failed"

    with Session(db) as session:
        statuses = sorted(
            j.status
            for j in session.exec(select(Job).where(Job.report_id == report_id)).all()
        )
        assert statuses == ["failed", "failed"]
        # Report row mirrors the failure too (so /resume can pick it up).
        report = session.get(Report, report_id)
        assert report is not None and report.stage == ReportStage.failed
        assert report.error and "Force-failed" in report.error

    # Idempotency: calling force_fail twice on the same report must error
    # rather than silently re-rewriting state.
    with Session(db) as session, pytest.raises(HTTPException) as exc:  # type: ignore[call-overload]
        force_fail(report_id, session=session)
    assert exc.value.status_code == 400


def test_force_fail_rejects_already_terminal_reports(db) -> None:  # type: ignore[no-untyped-def]
    """Can't force-fail a done report -- the user shouldn't think they
    achieved anything when the report has already shipped."""
    from fastapi import HTTPException

    from api.models import Report, ReportStage
    from api.routes.reports import force_fail

    report_id = _seed_report(db)
    with Session(db) as session:
        r = session.get(Report, report_id)
        assert r is not None
        r.stage = ReportStage.done
        session.add(r)
        session.commit()

    with Session(db) as session, pytest.raises(HTTPException) as exc:  # type: ignore[call-overload]
        force_fail(report_id, session=session)
    assert exc.value.status_code == 400
    assert "done" in exc.value.detail


# ----------------------------------------------------------------------
# /workers/status diagnostics
# ----------------------------------------------------------------------

def test_workers_status_surfaces_running_jobs_with_age_and_heartbeat(db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The whole point of the /workers page: a live job must report its
    age plus the gap since the last audit_log event. That's the silent-
    hang signal -- a job running for 5 minutes with no model_call event
    in the last 90s is the call that's actually wedged."""
    monkeypatch.setattr("api.routes.workers.engine", db, raising=False)

    from api.models import AuditLog, Job, ReportStage
    from api.routes.workers import status

    report_id = _seed_report(db)
    started_60s_ago = datetime.now(UTC) - timedelta(seconds=60)
    last_event_25s_ago = datetime.now(UTC) - timedelta(seconds=25)
    with Session(db) as session:
        session.add(Job(
            report_id=report_id, stage=ReportStage.draft,
            status="running", attempts=1, started_at=started_60s_ago,
        ))
        session.add(AuditLog(
            report_id=report_id, actor="macro-strategist", event="model_call",
            cost_usd=0.01, created_at=last_event_25s_ago,
        ))
        session.commit()

    with Session(db) as session:
        out = status(session=session)
    assert len(out.running) == 1
    j = out.running[0]
    assert j.report_id == report_id
    assert j.stage == ReportStage.draft
    # Age + heartbeat gap should be in the right ballpark (allow slack
    # for clock drift inside the test).
    assert j.age_seconds is not None and 50 <= j.age_seconds <= 80
    assert j.last_event_seconds_ago is not None and 15 <= j.last_event_seconds_ago <= 40
    assert j.last_event == "model_call"
    assert j.last_event_actor == "macro-strategist"
    # Below the standard-mode 15-min lease, so not flagged stuck.
    assert not j.is_stuck
    assert j.deadline_seconds is not None and j.deadline_seconds > 60


def test_workers_status_flags_stuck_jobs_past_lease(db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Standard-mode lease is 15 minutes -- past that and `is_stuck` flips
    so the dashboard can render it amber."""
    monkeypatch.setattr("api.routes.workers.engine", db, raising=False)

    from api.models import Job, ReportStage
    from api.routes.workers import status

    report_id = _seed_report(db)
    started_long_ago = datetime.now(UTC) - timedelta(minutes=20)
    with Session(db) as session:
        session.add(Job(
            report_id=report_id, stage=ReportStage.draft,
            status="running", attempts=1, started_at=started_long_ago,
        ))
        session.commit()

    with Session(db) as session:
        out = status(session=session)
    assert out.running[0].is_stuck


def test_workers_status_includes_pending_and_recent_failures(db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The page shows three buckets: running, pending (queued), and
    recent failures. Pending tells the user 'work is waiting'; recent
    failures tells them 'something just blew up'."""
    monkeypatch.setattr("api.routes.workers.engine", db, raising=False)

    from api.models import Job, ReportStage
    from api.routes.workers import status

    report_id = _seed_report(db)
    now = datetime.now(UTC)
    with Session(db) as session:
        session.add(Job(
            report_id=report_id, stage=ReportStage.research,
            status="pending", attempts=0,
        ))
        session.add(Job(
            report_id=report_id, stage=ReportStage.draft,
            status="failed", attempts=2,
            last_error="APITimeoutError: 120s",
            finished_at=now - timedelta(minutes=5),
        ))
        # Old failure -- should not appear in the recent-window bucket.
        session.add(Job(
            report_id=report_id, stage=ReportStage.brief,
            status="failed", attempts=1,
            last_error="ancient",
            finished_at=now - timedelta(hours=4),
        ))
        session.commit()

    with Session(db) as session:
        out = status(session=session)
    assert len(out.pending) == 1
    assert out.pending[0].stage == ReportStage.research
    assert len(out.recent_failures) == 1
    assert "APITimeoutError" in (out.recent_failures[0].last_error or "")


def test_workers_status_reports_worker_alive_when_recent_heartbeat(db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The worker process writes a heartbeat each poll cycle. /workers/status
    must surface it as `worker_alive=true` when fresh -- this is the signal
    that distinguishes 'worker idle' from 'worker dead'."""
    monkeypatch.setattr("api.routes.workers.engine", db, raising=False)

    import api.app_settings as app_settings_mod
    monkeypatch.setattr(app_settings_mod, "engine", db)

    from api.routes.workers import status

    app_settings_mod.record_worker_heartbeat()
    with Session(db) as session:
        out = status(session=session)
    assert out.worker_alive is True
    assert out.worker_last_seen_at is not None
    assert out.worker_last_seen_seconds_ago is not None
    assert out.worker_last_seen_seconds_ago < 5


def test_workers_status_reports_worker_offline_when_heartbeat_stale(db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A heartbeat older than the staleness threshold flips worker_alive
    to false -- the visible signal on the dashboard that the process is
    dead and a redeploy is needed."""
    monkeypatch.setattr("api.routes.workers.engine", db, raising=False)

    import api.app_settings as app_settings_mod
    monkeypatch.setattr(app_settings_mod, "engine", db)

    from api.models import AppSetting
    from api.routes.workers import status

    long_ago = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    with Session(db) as session:
        session.add(AppSetting(
            key=app_settings_mod.KEY_WORKER_HEARTBEAT, value=long_ago,
        ))
        session.commit()

    with Session(db) as session:
        out = status(session=session)
    assert out.worker_alive is False
    assert out.worker_last_seen_seconds_ago is not None
    assert out.worker_last_seen_seconds_ago > 60


def test_workers_status_reports_worker_offline_when_no_heartbeat(db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Fresh deploy / first-ever boot has no heartbeat row at all. Show
    that as offline rather than crashing or rendering as alive."""
    monkeypatch.setattr("api.routes.workers.engine", db, raising=False)

    import api.app_settings as app_settings_mod
    monkeypatch.setattr(app_settings_mod, "engine", db)

    from api.routes.workers import status

    with Session(db) as session:
        out = status(session=session)
    assert out.worker_alive is False
    assert out.worker_last_seen_at is None
    assert out.worker_last_seen_seconds_ago is None


def test_workers_force_fail_endpoint_routes_through_failure_path(db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Per-job force-fail must use the same _handle_failure path as a real
    exception so the next attempt gets queued (within MAX_ATTEMPTS).
    Otherwise the user clicks the button, the job dies, and the report
    sits in limbo with no retry."""
    monkeypatch.setattr("api.routes.workers.engine", db, raising=False)

    from api.models import Job, ReportStage
    from api.routes.workers import force_fail_job

    report_id = _seed_report(db)
    with Session(db) as session:
        session.add(Job(
            report_id=report_id, stage=ReportStage.draft,
            status="running", attempts=1,
            started_at=datetime.now(UTC),
        ))
        session.commit()
        running_id = session.exec(
            select(Job).where(Job.status == "running")
        ).first().id  # type: ignore[union-attr]

    with Session(db) as session:
        out = force_fail_job(running_id, session=session)
    assert out.status == "failed"

    with Session(db) as session:
        # A retry pending job should now exist (attempts<MAX_ATTEMPTS).
        pendings = session.exec(
            select(Job).where(Job.report_id == report_id, Job.status == "pending")
        ).all()
        assert len(pendings) == 1
        assert pendings[0].stage == ReportStage.draft
