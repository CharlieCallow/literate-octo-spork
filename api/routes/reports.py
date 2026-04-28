"""Report intake + status endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from api import storage
from api.auth import require_auth
from api.db import get_session
from api.models import AuditLog, Job, Report, ReportMode, ReportStage

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
    created_at: datetime

    @classmethod
    def from_db(cls, r: Report) -> ReportOut:
        # Postgres TIMESTAMP strips tzinfo on round-trip; re-attach UTC so the
        # serialised ISO string carries an offset and JS doesn't parse it as
        # local time. Existing rows are already in UTC -- they just lost the tag.
        created_at = r.created_at if r.created_at.tzinfo else r.created_at.replace(tzinfo=UTC)
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
            created_at=created_at,
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


@router.get("/{report_id}/charts", dependencies=[Depends(require_auth)])
def list_chart_files(report_id: int) -> list[dict[str, object]]:
    """List chart filenames generated for a report (PNG + JSON sidecars).
    Falls back to R2 when the local FS doesn't have any."""
    from api.workflow.state_machine import working_dir
    wd = working_dir(report_id)
    charts_dir = wd / "charts"
    local_files = sorted(charts_dir.glob("*.png")) if charts_dir.exists() else []
    if local_files:
        return [
            {"filename": p.name, "has_json": p.with_suffix(".json").exists()}
            for p in local_files
        ]
    # No local charts -- check R2.
    r2_files = storage.list_chart_files(report_id)
    return [
        {"filename": name, "has_json": storage.object_exists(report_id, "charts", name.replace(".png", ".json"))}
        for name in r2_files
    ]


@router.get("/{report_id}/chart.json", dependencies=[Depends(require_auth)])
def get_chart_json(
    report_id: int,
    filename: str,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    """Return the JSON sidecar for a chart so the dashboard can render it
    interactively. Falls back to R2 if the local file is gone."""
    import json as _json

    report = session.get(Report, report_id)
    if not report:
        raise HTTPException(404, "Report not found")

    from api.workflow.state_machine import working_dir
    wd = working_dir(report_id)
    base = filename.rsplit(".", 1)[0]
    json_path = wd / "charts" / f"{base}.json"

    if not json_path.exists():
        # Try R2 fetch into the local cache so future calls are fast.
        fetched = storage.fetch_to_local(report_id, wd, "charts", f"{base}.json")
        if fetched is None:
            raise HTTPException(404, "Chart sidecar not found")
        json_path = fetched

    return _json.loads(json_path.read_text(encoding="utf-8"))


@router.get("/{report_id}/pdf", dependencies=[Depends(require_auth)])
def get_pdf(report_id: int, session: Session = Depends(get_session)):  # type: ignore[no-untyped-def]
    report = session.get(Report, report_id)
    if not report:
        raise HTTPException(404, "Report not found")

    # Fast path: local file is present, serve it directly.
    if report.pdf_path:
        path = Path(report.pdf_path)
        if path.exists():
            return FileResponse(
                path,
                media_type="application/pdf",
                filename=path.name,
                headers={
                    "Content-Disposition": f'inline; filename="{path.name}"',
                    "Cache-Control": "private, max-age=3600",
                },
            )

    # Local file missing. If R2 has it, redirect to a signed URL. The 302 is
    # cacheable; the browser/iframe stops bouncing through the API.
    if report.stage == ReportStage.done and storage.object_exists(report_id, "report.pdf"):
        url = storage.signed_url(report_id, "report.pdf")
        if url:
            return RedirectResponse(url, status_code=302)

    if not report.pdf_path:
        raise HTTPException(404, "PDF not ready")

    # Local was set but the file's gone and R2 doesn't have it. Clear pdf_path
    # so the UI shows 'Re-run from brief' instead of a broken iframe.
    report.pdf_path = None
    session.add(report)
    session.commit()
    raise HTTPException(404, "PDF missing on disk")


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


class StageEstimate(BaseModel):
    stage: ReportStage
    seconds: float


@router.get("/eta/stage_durations", response_model=list[StageEstimate], dependencies=[Depends(require_auth)])
def stage_durations(session: Session = Depends(get_session)) -> list[StageEstimate]:
    """Average per-stage duration in seconds, computed from completed Jobs.
    The frontend uses this to compute a smarter ETA than the static estimate."""
    rows = session.exec(
        select(Job).where(
            Job.status == "done",
            Job.started_at.is_not(None),  # type: ignore[union-attr]
            Job.finished_at.is_not(None),  # type: ignore[union-attr]
        )
    ).all()
    by_stage: dict[ReportStage, list[float]] = {}
    for j in rows:
        if j.started_at and j.finished_at:
            dur = (j.finished_at - j.started_at).total_seconds()
            if 0 < dur < 3600:  # ignore outliers / clock skew
                by_stage.setdefault(j.stage, []).append(dur)
    return [
        StageEstimate(stage=s, seconds=sum(xs) / len(xs))
        for s, xs in by_stage.items()
    ]


@router.post("/{report_id}/resume", response_model=ReportOut, dependencies=[Depends(require_auth)])
def resume(
    report_id: int,
    from_stage: ReportStage | None = None,
    clean_slate: bool = False,
    session: Session = Depends(get_session),
) -> ReportOut:
    """Re-queue a failed or cancelled report. By default resumes from the last
    attempted stage; pass ?from_stage=research (or any earlier stage) to redo
    work from there. ?clean_slate=true wipes the working directory first so
    no stale artifacts remain. Workflow stages are idempotent."""
    report = session.get(Report, report_id)
    if not report:
        raise HTTPException(404, "Report not found")
    # Resume is allowed for failed/cancelled reports, and also for done reports
    # whose files have been wiped (Railway rebuild) -- in that case the user
    # explicitly chose to re-run via the "Re-run from brief" UI.
    pdf_missing = report.stage == ReportStage.done and not report.pdf_path
    if report.stage not in (ReportStage.failed, ReportStage.cancelled) and not pdf_missing:
        raise HTTPException(400, f"Report is not failed/cancelled (stage={report.stage})")

    if from_stage is not None:
        if from_stage in (ReportStage.queued, ReportStage.done, ReportStage.failed, ReportStage.cancelled):
            raise HTTPException(400, f"from_stage must be a workflow stage, not {from_stage}")
        resume_stage: ReportStage = from_stage
    else:
        last_job = session.exec(
            select(Job).where(Job.report_id == report_id).order_by(Job.created_at.desc())  # type: ignore[attr-defined]
        ).first()
        resume_stage = last_job.stage if last_job else ReportStage.brief

    if clean_slate:
        # Nuke the working directory so the agent doesn't pick up stale notes.
        import shutil

        from api.workflow.state_machine import working_dir
        wd = working_dir(report_id)
        if wd.exists():
            shutil.rmtree(wd)
        # Also clear pdf_path so the dashboard stops showing the old one.
        report.pdf_path = None

    report.stage = resume_stage
    report.error = None
    session.add(report)
    session.add(Job(report_id=report_id, stage=resume_stage))
    session.commit()
    session.refresh(report)
    return ReportOut.from_db(report)


class AuditEntry(BaseModel):
    id: int
    actor: str
    event: str
    cost_usd: float
    details: dict[str, object]
    created_at: datetime


@router.get("/{report_id}/audit", response_model=list[AuditEntry], dependencies=[Depends(require_auth)])
def list_audit(
    report_id: int,
    event: str | None = None,
    actor: str | None = None,
    session: Session = Depends(get_session),
) -> list[AuditEntry]:
    """Per-report audit log: every model call, every tool invocation,
    each with its own cost increment."""
    q = select(AuditLog).where(AuditLog.report_id == report_id)
    if event:
        q = q.where(AuditLog.event == event)
    if actor:
        q = q.where(AuditLog.actor == actor)
    rows = session.exec(q.order_by(AuditLog.created_at)).all()  # type: ignore[arg-type]
    return [
        AuditEntry(
            id=r.id or 0,
            actor=r.actor,
            event=r.event,
            cost_usd=r.cost_usd,
            details=dict(r.details or {}),
            created_at=r.created_at if r.created_at.tzinfo else r.created_at.replace(tzinfo=UTC),
        )
        for r in rows
    ]


@router.post("/{report_id}/cancel", response_model=ReportOut, dependencies=[Depends(require_auth)])
def cancel(report_id: int, session: Session = Depends(get_session)) -> ReportOut:
    """Mark a running report as cancelled. The worker checks this flag before
    claiming the next stage; an in-flight stage will finish but no further
    stages will run."""
    report = session.get(Report, report_id)
    if not report:
        raise HTTPException(404, "Report not found")
    if report.stage in (ReportStage.done, ReportStage.failed, ReportStage.cancelled):
        raise HTTPException(400, f"Report is already {report.stage}")

    report.stage = ReportStage.cancelled
    report.error = "Cancelled by user"
    session.add(report)

    # Drop any pending jobs for this report so the worker doesn't pick them up.
    pending = session.exec(
        select(Job).where(Job.report_id == report_id, Job.status == "pending")
    ).all()
    for j in pending:
        j.status = "failed"
        j.last_error = "Cancelled by user"
        j.finished_at = datetime.now()
        session.add(j)

    session.commit()
    session.refresh(report)
    return ReportOut.from_db(report)
