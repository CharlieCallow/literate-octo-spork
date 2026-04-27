"""Idempotent stage runner. Pulls a job, runs the stage, queues the next.

Failures retry up to MAX_ATTEMPTS times with exponential backoff (2^n seconds);
beyond that the report is marked failed and waits for a manual /resume."""

from __future__ import annotations

import logging
import traceback
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from api.db import engine
from api.models import Job, Report, ReportStage
from api.workflow.state_machine import run_stage

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
BACKOFF_BASE_S = 2  # 2s, 4s, 8s


def claim_one_job() -> Job | None:
    """Pop the oldest pending job whose run_after window has elapsed."""
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        job = session.exec(
            select(Job)
            .where(Job.status == "pending")
            .where((Job.run_after.is_(None)) | (Job.run_after <= now))  # type: ignore[union-attr]
            .order_by(Job.created_at)
        ).first()
        if not job:
            return None
        job.status = "running"
        job.started_at = now
        job.attempts += 1
        session.add(job)
        session.commit()
        session.refresh(job)
        return job


def execute(job: Job) -> None:
    with Session(engine) as session:
        report = session.get(Report, job.report_id)
        if not report:
            _fail_job(session, job, "Report missing")
            return
        report.stage = job.stage
        report.error = None  # clear previous failure on retry
        session.add(report)
        session.commit()
        session.refresh(report)
        cost_at_start = report.cost_usd

    try:
        next_stage = run_stage(report, job.stage)
    except Exception as e:  # noqa: BLE001
        log.exception("stage %s failed for report %s", job.stage, report.id)
        _handle_failure(job, e)
        return

    with Session(engine) as session:
        r = session.get(Report, job.report_id)
        j = session.get(Job, job.id)
        if r:
            r.stage = next_stage
            session.add(r)
        if j:
            j.status = "done"
            j.finished_at = datetime.now(timezone.utc)
            j.cost_usd = (r.cost_usd if r else cost_at_start) - cost_at_start
            session.add(j)
        if next_stage != ReportStage.done:
            session.add(Job(report_id=job.report_id, stage=next_stage))
        session.commit()


def _handle_failure(job: Job, e: Exception) -> None:
    """Mark job failed; retry with backoff if we have attempts left, else fail report."""
    err = f"{type(e).__name__}: {e}"
    tb = traceback.format_exc()
    now = datetime.now(timezone.utc)

    with Session(engine) as session:
        j = session.get(Job, job.id)
        if j:
            j.status = "failed"
            j.last_error = err
            j.finished_at = now
            session.add(j)

        # job.attempts was incremented at claim time, so it now counts THIS attempt.
        if job.attempts < MAX_ATTEMPTS:
            backoff = BACKOFF_BASE_S * (2 ** (job.attempts - 1))  # 2, 4, 8...
            run_after = now + timedelta(seconds=backoff)
            session.add(Job(
                report_id=job.report_id,
                stage=job.stage,
                attempts=job.attempts,  # carry forward
                run_after=run_after,
            ))
            log.warning(
                "report %s stage %s failed (attempt %d/%d) -- retrying in %ds",
                job.report_id, job.stage, job.attempts, MAX_ATTEMPTS, backoff,
            )
        else:
            r = session.get(Report, job.report_id)
            if r:
                r.stage = ReportStage.failed
                r.error = f"{err}\n{tb}"
                session.add(r)
            log.error("report %s stage %s exhausted retries", job.report_id, job.stage)
        session.commit()


def _fail_job(session: Session, job: Job, msg: str) -> None:
    job.status = "failed"
    job.last_error = msg
    job.finished_at = datetime.now(timezone.utc)
    session.add(job)
    session.commit()
