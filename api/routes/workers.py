"""Worker / job diagnostics.

A live view of what the worker(s) are doing, exposed for the /workers
dashboard page. The point is to make hangs visible -- a job in `running`
for 8 minutes with no recent audit-log activity is the silent-failure
mode, and the only way to triage it is to see it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc
from sqlmodel import Session, select

from api import app_settings
from api.auth import require_auth
from api.db import get_session
from api.models import AuditLog, Job, Report, ReportMode, ReportStage
from api.workflow.runner import (
    _DEFAULT_STUCK_DEADLINE_S,
    STUCK_JOB_DEADLINE_S,
    _handle_failure,
)

# Heartbeat staleness threshold. Worker writes one each poll cycle (3s).
# 30s gives plenty of slack for a slow DB write or a pause inside a stage
# while still catching a dead process before the user has burned 5 min
# wondering why nothing's happening.
_WORKER_DEAD_AFTER_S = 30.0

router = APIRouter(prefix="/workers", tags=["workers"])

# How far back to surface "recent" failed/cancelled jobs and audit events.
# An hour gives enough context to see what just broke without flooding
# the page with the day's history.
_RECENT_WINDOW = timedelta(hours=1)


class JobActivity(BaseModel):
    job_id: int
    report_id: int
    report_theme: str
    report_mode: ReportMode
    stage: ReportStage
    status: str
    attempts: int
    started_at: datetime | None
    finished_at: datetime | None
    last_error: str | None
    cost_usd: float
    # Liveness signals -- the dashboard renders these so a hung call is
    # visually obvious without the user having to dig.
    age_seconds: float | None  # for running: now - started_at; else None
    last_event_seconds_ago: float | None  # gap since the latest audit_log row
    last_event: str | None  # "model_call" / "tool_call" / "rate_limit" / etc.
    last_event_actor: str | None  # which agent emitted it
    deadline_seconds: int | None  # the watchdog's lease for this stage
    is_stuck: bool  # past deadline -> True


class SupervisorStats(BaseModel):
    """Summary of the in-process supervisor that auto-respawns the worker
    subprocess on crash. Only populated when BUNDLE_WORKER is on."""
    enabled: bool
    crash_count: int = 0
    consecutive_failures: int = 0
    last_exit_code: int | None = None
    last_crash_at: datetime | None = None
    last_spawn_at: datetime | None = None
    pid: int | None = None


class WorkerErrorInfo(BaseModel):
    """Most recent unhandled exception captured by the worker's outer
    try/except. The poll loop logs and continues now, but persists the
    traceback so /workers can render it."""
    message: str
    captured_at: datetime | None


class WorkersStatus(BaseModel):
    now: datetime
    last_activity_at: datetime | None
    last_activity_seconds_ago: float | None
    # Worker-process liveness, distinct from agent activity. The worker
    # writes a heartbeat each poll cycle so we can tell "alive but idle"
    # apart from "process is dead". `worker_alive=False` means the
    # heartbeat is missing or stale past _WORKER_DEAD_AFTER_S.
    worker_last_seen_at: datetime | None
    worker_last_seen_seconds_ago: float | None
    worker_alive: bool
    worker_last_error: WorkerErrorInfo | None
    supervisor: SupervisorStats
    running: list[JobActivity]
    pending: list[JobActivity]
    recent_failures: list[JobActivity]


def _aware(dt: datetime | None) -> datetime | None:
    """Render any datetime as UTC-aware so `now - dt` doesn't blow up.

    Postgres TIMESTAMP columns strip tz on round-trip so even rows we
    wrote with `datetime.now(UTC)` come back naive."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _activity(
    j: Job, report: Report | None, now: datetime, last_event: AuditLog | None,
) -> JobActivity:
    started = _aware(j.started_at)
    age: float | None = None
    if j.status == "running" and started is not None:
        age = max(0.0, (now - started).total_seconds())

    last_evt_at = _aware(last_event.created_at) if last_event else None
    last_evt_gap: float | None = None
    if last_evt_at is not None:
        last_evt_gap = max(0.0, (now - last_evt_at).total_seconds())

    mode = report.mode if report else ReportMode.standard
    deadline = STUCK_JOB_DEADLINE_S.get(mode, _DEFAULT_STUCK_DEADLINE_S)
    is_stuck = bool(j.status == "running" and age is not None and age >= deadline)

    return JobActivity(
        job_id=j.id or 0,
        report_id=j.report_id,
        report_theme=(report.theme if report else f"<missing report {j.report_id}>"),
        report_mode=mode,
        stage=j.stage,
        status=j.status,
        attempts=j.attempts,
        started_at=started,
        finished_at=_aware(j.finished_at),
        last_error=j.last_error,
        cost_usd=j.cost_usd,
        age_seconds=age,
        last_event_seconds_ago=last_evt_gap,
        last_event=last_event.event if last_event else None,
        last_event_actor=last_event.actor if last_event else None,
        deadline_seconds=deadline,
        is_stuck=is_stuck,
    )


def _latest_audit_for_report(session: Session, report_id: int) -> AuditLog | None:
    return session.exec(
        select(AuditLog)
        .where(AuditLog.report_id == report_id)
        .order_by(desc(AuditLog.created_at))
        .limit(1)
    ).first()


@router.get("/status", response_model=WorkersStatus, dependencies=[Depends(require_auth)])
def status(session: Session = Depends(get_session)) -> WorkersStatus:
    """Live snapshot of in-flight + recently-finished jobs.

    Driven by the `/workers` dashboard page which polls this every few
    seconds while a report is running."""
    now = datetime.now(UTC)
    cutoff = now - _RECENT_WINDOW

    running_jobs = session.exec(
        select(Job).where(Job.status == "running").order_by(Job.started_at)
    ).all()
    pending_jobs = session.exec(
        select(Job)
        .where(Job.status == "pending")
        .order_by(Job.created_at)
        .limit(10)
    ).all()
    recent_failed = session.exec(
        select(Job)
        .where(Job.status == "failed")
        .where(Job.finished_at >= cutoff)  # type: ignore[operator]
        .order_by(desc(Job.finished_at))
        .limit(20)
    ).all()

    # Pre-fetch reports + per-report latest audit log so the response
    # avoids an N+1.
    report_ids = {j.report_id for j in (*running_jobs, *pending_jobs, *recent_failed)}
    reports_by_id: dict[int, Report] = {
        r.id: r  # type: ignore[misc]
        for r in session.exec(select(Report).where(Report.id.in_(report_ids))).all()  # type: ignore[union-attr]
        if r.id is not None
    }
    last_events_by_report: dict[int, AuditLog | None] = {}
    for rid in report_ids:
        last_events_by_report[rid] = _latest_audit_for_report(session, rid)

    last_overall_event = session.exec(
        select(AuditLog).order_by(desc(AuditLog.created_at)).limit(1)
    ).first()
    last_at = _aware(last_overall_event.created_at) if last_overall_event else None
    last_gap = max(0.0, (now - last_at).total_seconds()) if last_at else None

    worker_seen = app_settings.worker_last_seen()
    worker_gap = max(0.0, (now - worker_seen).total_seconds()) if worker_seen else None
    worker_alive = worker_gap is not None and worker_gap < _WORKER_DEAD_AFTER_S

    last_err = app_settings.worker_last_error()
    worker_last_error = (
        WorkerErrorInfo(message=last_err[0], captured_at=_aware(last_err[1]))
        if last_err else None
    )

    # Supervisor stats. The supervisor runs in the API process so even when
    # the worker is dead we can still tell the user "it's crashed N times".
    from api.workflow.supervisor import current as current_supervisor
    sv = current_supervisor()
    if sv is None:
        supervisor = SupervisorStats(enabled=False)
    else:
        supervisor = SupervisorStats(
            enabled=True,
            crash_count=sv.stats.crash_count,
            consecutive_failures=sv.stats.consecutive_failures,
            last_exit_code=sv.stats.last_exit_code,
            last_crash_at=_aware(sv.stats.last_crash_at),
            last_spawn_at=_aware(sv.stats.last_spawn_at),
            pid=sv.stats.pid,
        )

    def _build(jobs):  # type: ignore[no-untyped-def]
        return [
            _activity(j, reports_by_id.get(j.report_id), now,
                      last_events_by_report.get(j.report_id))
            for j in jobs
        ]

    return WorkersStatus(
        now=now,
        last_activity_at=last_at,
        last_activity_seconds_ago=last_gap,
        worker_last_seen_at=worker_seen,
        worker_last_seen_seconds_ago=worker_gap,
        worker_alive=worker_alive,
        worker_last_error=worker_last_error,
        supervisor=supervisor,
        running=_build(running_jobs),
        pending=_build(pending_jobs),
        recent_failures=_build(recent_failed),
    )


@router.post("/jobs/{job_id}/force_fail", response_model=JobActivity, dependencies=[Depends(require_auth)])
def force_fail_job(job_id: int, session: Session = Depends(get_session)) -> JobActivity:
    """Triage knob: mark a single running job as failed via the standard
    failure path. Used by the /workers page to unstick one job without
    nuking the whole report's other in-flight stages."""
    j = session.get(Job, job_id)
    if j is None:
        raise HTTPException(404, "Job not found")
    if j.status != "running":
        raise HTTPException(400, f"Job is not running (status={j.status})")

    # Detach from this session so _handle_failure works in its own session
    # without surprising us with a state conflict on the next read.
    job_snapshot = j
    session.expunge(j)

    _handle_failure(job_snapshot, RuntimeError("force-failed via /workers"))

    j2 = session.get(Job, job_id)
    report = session.get(Report, j2.report_id) if j2 else None
    return _activity(j2 or job_snapshot, report, datetime.now(UTC), None)
