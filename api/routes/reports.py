"""Report intake + status endpoints."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from api.auth import require_auth
from api.db import get_session
from api.models import Job, Report, ReportMode, ReportStage

router = APIRouter(prefix="/reports", tags=["reports"])


class CreateReport(BaseModel):
    theme: str
    subtitle: str | None = None
    mode: ReportMode = ReportMode.standard
    team_override: list[str] = []          # contributor slugs; empty = let EIC pick
    budget_cap_usd: float | None = None    # overrides global per-report cap


class ReportOut(BaseModel):
    id: int
    theme: str
    subtitle: str | None
    mode: ReportMode
    budget_cap_usd: float | None
    team_override: list[str]
    stage: ReportStage
    error: str | None
    cost_usd: float
    pdf_url: str | None

    @classmethod
    def from_db(cls, r: Report) -> ReportOut:
        return cls(
            id=r.id or 0,
            theme=r.theme,
            subtitle=r.subtitle,
            mode=r.mode,
            budget_cap_usd=r.budget_cap_usd,
            team_override=list(r.team_override or []),
            stage=r.stage,
            error=r.error,
            cost_usd=r.cost_usd,
            pdf_url=f"/reports/{r.id}/pdf" if r.pdf_path else None,
        )


@router.post("", response_model=ReportOut, dependencies=[Depends(require_auth)])
def create(payload: CreateReport, session: Session = Depends(get_session)) -> ReportOut:
    # Filter team override against the actual roster.
    from api.workflow.state_machine import get_roster_map
    roster_map = get_roster_map()
    team = [s for s in payload.team_override if s in roster_map]

    report = Report(
        theme=payload.theme,
        subtitle=payload.subtitle,
        mode=payload.mode,
        team_override=team,
        budget_cap_usd=payload.budget_cap_usd,
    )
    session.add(report)
    session.commit()
    session.refresh(report)

    job = Job(report_id=report.id or 0, stage=ReportStage.brief)
    session.add(job)
    session.commit()
    return ReportOut.from_db(report)


@router.get("", response_model=list[ReportOut], dependencies=[Depends(require_auth)])
def list_reports(session: Session = Depends(get_session)) -> list[ReportOut]:
    rows = session.exec(select(Report).order_by(Report.created_at.desc())).all()
    return [ReportOut.from_db(r) for r in rows]


@router.get("/{report_id}", response_model=ReportOut, dependencies=[Depends(require_auth)])
def get_report(report_id: int, session: Session = Depends(get_session)) -> ReportOut:
    report = session.get(Report, report_id)
    if not report:
        raise HTTPException(404, "Report not found")
    return ReportOut.from_db(report)


@router.get("/{report_id}/pdf", dependencies=[Depends(require_auth)])
def get_pdf(report_id: int, session: Session = Depends(get_session)) -> FileResponse:
    report = session.get(Report, report_id)
    if not report or not report.pdf_path:
        raise HTTPException(404, "PDF not ready")
    path = Path(report.pdf_path)
    if not path.exists():
        raise HTTPException(404, "PDF missing on disk")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=path.name,
        headers={
            # Inline so the browser embeds it instead of prompting download.
            "Content-Disposition": f'inline; filename="{path.name}"',
            # Cache aggressively while a report exists -- PDFs are immutable
            # once written. Stops the iframe re-fetching every poll tick.
            "Cache-Control": "private, max-age=3600",
        },
    )


class JobOut(BaseModel):
    id: int
    stage: ReportStage
    status: str
    attempts: int
    cost_usd: float
    last_error: str | None
    started_at: datetime | None
    finished_at: datetime | None


@router.get("/{report_id}/jobs", response_model=list[JobOut], dependencies=[Depends(require_auth)])
def list_jobs(report_id: int, session: Session = Depends(get_session)) -> list[JobOut]:
    rows = session.exec(
        select(Job).where(Job.report_id == report_id).order_by(Job.created_at)
    ).all()
    return [
        JobOut(
            id=j.id or 0,
            stage=j.stage,
            status=j.status,
            attempts=j.attempts,
            cost_usd=j.cost_usd,
            last_error=j.last_error,
            started_at=j.started_at,
            finished_at=j.finished_at,
        )
        for j in rows
    ]


@router.post("/{report_id}/resume", response_model=ReportOut, dependencies=[Depends(require_auth)])
def resume(report_id: int, session: Session = Depends(get_session)) -> ReportOut:
    """Re-queue a failed report from its last attempted stage. Workflow stages
    are idempotent so this safely overwrites prior outputs without corruption."""
    report = session.get(Report, report_id)
    if not report:
        raise HTTPException(404, "Report not found")
    if report.stage != ReportStage.failed:
        raise HTTPException(400, f"Report is not failed (stage={report.stage})")

    # Last attempted stage is whatever the most recent job ran.
    last_job = session.exec(
        select(Job).where(Job.report_id == report_id).order_by(Job.created_at.desc())  # type: ignore[attr-defined]
    ).first()
    resume_stage = last_job.stage if last_job else ReportStage.brief

    report.stage = resume_stage
    report.error = None
    session.add(report)
    session.add(Job(report_id=report_id, stage=resume_stage))
    session.commit()
    session.refresh(report)
    return ReportOut.from_db(report)
