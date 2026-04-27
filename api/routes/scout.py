"""Scout endpoints: list themes, manual run, commission a theme into a report."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from api.auth import require_auth
from api.db import get_session
from api.models import Job, Report, ReportMode, ReportStage, ScoutRun, Theme
from api.scout_runner import run_scout

router = APIRouter(prefix="/scout", tags=["scout"])


class ThemeOut(BaseModel):
    id: int
    scout_run_id: int
    headline: str
    why_now: str
    dig_into: str
    source_urls: list[str]
    score: float
    surfaced_at: datetime
    commissioned_report_id: int | None

    @classmethod
    def from_db(cls, t: Theme) -> ThemeOut:
        return cls(
            id=t.id or 0,
            scout_run_id=t.scout_run_id,
            headline=t.headline,
            why_now=t.why_now,
            dig_into=t.dig_into,
            source_urls=list(t.source_urls or []),
            score=t.score,
            surfaced_at=t.surfaced_at,
            commissioned_report_id=t.commissioned_report_id,
        )


class ScoutRunOut(BaseModel):
    id: int
    started_at: datetime
    finished_at: datetime | None
    n_themes: int
    cost_usd: float
    error: str | None


@router.get("/themes", response_model=list[ThemeOut], dependencies=[Depends(require_auth)])
def latest_themes(session: Session = Depends(get_session)) -> list[ThemeOut]:
    """Return the themes from the most recent ScoutRun (or empty list)."""
    last_run = session.exec(
        select(ScoutRun).order_by(ScoutRun.started_at.desc())  # type: ignore[attr-defined]
    ).first()
    if not last_run or last_run.id is None:
        return []
    rows = session.exec(
        select(Theme).where(Theme.scout_run_id == last_run.id).order_by(Theme.id)  # type: ignore[arg-type]
    ).all()
    return [ThemeOut.from_db(t) for t in rows]


@router.get("/runs", response_model=list[ScoutRunOut], dependencies=[Depends(require_auth)])
def list_runs(session: Session = Depends(get_session)) -> list[ScoutRunOut]:
    rows = session.exec(
        select(ScoutRun).order_by(ScoutRun.started_at.desc())  # type: ignore[attr-defined]
    ).all()
    return [
        ScoutRunOut(
            id=r.id or 0,
            started_at=r.started_at,
            finished_at=r.finished_at,
            n_themes=r.n_themes,
            cost_usd=r.cost_usd,
            error=r.error,
        )
        for r in rows
    ]


@router.post("/run", dependencies=[Depends(require_auth)])
def kick_run(background: BackgroundTasks) -> dict[str, str]:
    """Manually trigger a Scout digest in the background."""
    background.add_task(run_scout)
    return {"status": "started"}


class CommissionPayload(BaseModel):
    mode: ReportMode = ReportMode.standard
    budget_cap_usd: float | None = None


@router.post(
    "/themes/{theme_id}/commission",
    dependencies=[Depends(require_auth)],
)
def commission(
    theme_id: int,
    payload: CommissionPayload,
    session: Session = Depends(get_session),
) -> dict[str, int | str]:
    """Spawn a Report from a Theme and link them. Returns the new report id."""
    theme = session.get(Theme, theme_id)
    if not theme:
        raise HTTPException(404, "Theme not found")
    if theme.commissioned_report_id:
        return {"report_id": theme.commissioned_report_id, "status": "already_commissioned"}

    report = Report(
        theme=theme.headline,
        subtitle=theme.why_now or None,
        mode=payload.mode,
        budget_cap_usd=payload.budget_cap_usd,
    )
    session.add(report)
    session.commit()
    session.refresh(report)

    theme.commissioned_report_id = report.id
    session.add(theme)

    job = Job(report_id=report.id or 0, stage=ReportStage.brief)
    session.add(job)
    session.commit()
    return {"report_id": report.id or 0, "status": "commissioned"}
