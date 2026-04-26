"""Idempotent stage runner. Pulls a job, runs the stage, queues the next."""

from __future__ import annotations

import logging
import traceback
from datetime import datetime, timezone

from sqlmodel import Session, select

from api.db import engine
from api.models import Job, Report, ReportStage
from api.workflow.state_machine import run_stage

log = logging.getLogger(__name__)


def claim_one_job() -> Job | None:
    """Pop the oldest pending job. Naive single-worker locking — fine for M1."""
    with Session(engine) as session:
        job = session.exec(
            select(Job).where(Job.status == "pending").order_by(Job.created_at)
        ).first()
        if not job:
            return None
        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
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
        session.add(report)
        session.commit()
        session.refresh(report)

    try:
        next_stage = run_stage(report, job.stage)
    except Exception as e:  # noqa: BLE001
        log.exception("stage %s failed for report %s", job.stage, report.id)
        with Session(engine) as session:
            r = session.get(Report, job.report_id)
            j = session.get(Job, job.id)
            if r:
                r.stage = ReportStage.failed
                r.error = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
                session.add(r)
            if j:
                j.status = "failed"
                j.last_error = str(e)
                j.finished_at = datetime.now(timezone.utc)
                session.add(j)
            session.commit()
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
            session.add(j)
        if next_stage != ReportStage.done:
            session.add(Job(report_id=job.report_id, stage=next_stage))
        session.commit()


def _fail_job(session: Session, job: Job, msg: str) -> None:
    job.status = "failed"
    job.last_error = msg
    job.finished_at = datetime.now(timezone.utc)
    session.add(job)
    session.commit()
