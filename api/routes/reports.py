"""Report intake + status endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from api.auth import require_auth
from api.db import get_session
from api.models import Job, Report, ReportStage

router = APIRouter(prefix="/reports", tags=["reports"])


class CreateReport(BaseModel):
    theme: str
    subtitle: str | None = None


class ReportOut(BaseModel):
    id: int
    theme: str
    subtitle: str | None
    stage: ReportStage
    error: str | None
    cost_usd: float
    pdf_url: str | None

    @classmethod
    def from_db(cls, r: Report) -> "ReportOut":
        return cls(
            id=r.id or 0,
            theme=r.theme,
            subtitle=r.subtitle,
            stage=r.stage,
            error=r.error,
            cost_usd=r.cost_usd,
            pdf_url=f"/reports/{r.id}/pdf" if r.pdf_path else None,
        )


@router.post("", response_model=ReportOut, dependencies=[Depends(require_auth)])
def create(payload: CreateReport, session: Session = Depends(get_session)) -> ReportOut:
    report = Report(theme=payload.theme, subtitle=payload.subtitle)
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
    return FileResponse(path, media_type="application/pdf", filename=path.name)
