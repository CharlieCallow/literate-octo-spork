"""Report intake + status endpoints."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import BaseModel
from sqlmodel import Session, select

from api import storage, uploads
from api.auth import require_auth
from api.db import get_session
from api.models import AuditLog, Job, Report, ReportMode, ReportStage, UploadedDocument
from api.settings import settings

router = APIRouter(prefix="/reports", tags=["reports"])


class CreateReport(BaseModel):
    theme: str
    subtitle: str | None = None
    mode: ReportMode = ReportMode.standard
    team_override: list[str] = []          # contributor slugs; empty = let EIC pick
    budget_cap_usd: float | None = None    # overrides global per-report cap
    # Optional render toggles. See Report.render_options for recognised keys.
    render_options: dict[str, bool] = {}


class ReportOut(BaseModel):
    id: int
    theme: str
    subtitle: str | None
    mode: ReportMode
    budget_cap_usd: float | None
    team_override: list[str]
    contributor_slugs: list[str]
    stage: ReportStage
    error: str | None
    cost_usd: float
    pdf_url: str | None
    max_domain_share: float | None
    top_domain: str | None
    is_test: bool
    word_count: int | None
    read_minutes: int | None
    claim_density: float | None
    # Theme-graph tags (set in housekeeping). Surfaced so the archive
    # search can match by ticker / theme without re-fetching.
    mentioned_tickers: list[str]
    mentioned_themes: list[str]
    render_options: dict[str, bool]
    created_at: datetime

    @classmethod
    def from_db(cls, r: Report, *, check_storage: bool = False) -> ReportOut:
        # Postgres TIMESTAMP strips tzinfo on round-trip; re-attach UTC so the
        # serialised ISO string carries an offset and JS doesn't parse it as
        # local time. Existing rows are already in UTC -- they just lost the tag.
        created_at = r.created_at if r.created_at.tzinfo else r.created_at.replace(tzinfo=UTC)
        # On Railway the working dir is wiped on every rebuild, which nulls
        # pdf_path even though the PDF still lives in R2. The /pdf route
        # already redirects to a signed URL in that case -- expose pdf_url so
        # the dashboard doesn't fall back to "re-run from brief".
        has_pdf = bool(r.pdf_path)
        if not has_pdf and check_storage and r.id and r.stage == ReportStage.done:
            has_pdf = storage.object_exists(r.id, "report.pdf")
        return cls(
            id=r.id or 0,
            theme=r.theme,
            subtitle=r.subtitle,
            mode=r.mode,
            budget_cap_usd=r.budget_cap_usd,
            team_override=list(r.team_override or []),
            contributor_slugs=list(r.contributor_slugs or []),
            stage=r.stage,
            error=r.error,
            cost_usd=r.cost_usd,
            pdf_url=f"/reports/{r.id}/pdf" if has_pdf else None,
            max_domain_share=r.max_domain_share,
            top_domain=r.top_domain,
            is_test=bool(r.is_test),
            word_count=r.word_count,
            read_minutes=r.read_minutes,
            claim_density=r.claim_density,
            mentioned_tickers=list(r.mentioned_tickers or []),
            mentioned_themes=list(r.mentioned_themes or []),
            render_options={k: bool(v) for k, v in (r.render_options or {}).items()},
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
        render_options={k: bool(v) for k, v in (payload.render_options or {}).items()},
        # Test-mode runs are throwaway smoke tests -- mark them so they
        # don't pollute Scout's "past reports" anchor / archive default /
        # performance ledger / house view.
        is_test=(payload.mode == ReportMode.test),
    )
    session.add(report)
    session.commit()
    session.refresh(report)

    job = Job(report_id=report.id or 0, stage=ReportStage.brief)
    session.add(job)
    session.commit()
    return ReportOut.from_db(report)


@router.get("/feed.xml")
def feed(session: Session = Depends(get_session)) -> Response:
    """RSS 2.0 feed of recently-completed reports.

    Public (no auth) by design -- subscription readers don't carry
    Forte's basic-auth header. Each item links to the report's public
    share URL when one's been minted; otherwise to the dashboard URL
    (which gates on auth, so an unauthenticated reader will see the
    sign-in prompt). Excludes test-mode and unfinished runs."""
    cutoff = datetime.now(UTC) - timedelta(days=180)
    rows = session.exec(
        select(Report)
        .where(Report.stage == ReportStage.done)
        .where(Report.is_test == False)  # noqa: E712
        .where(Report.created_at >= cutoff)
        .order_by(Report.created_at.desc())  # type: ignore[attr-defined]
        .limit(50)
    ).all()

    from xml.sax.saxutils import escape as _xml_escape
    base = (settings.public_base_url or "").rstrip("/")
    now_str = datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S +0000")

    def _item(r: Report) -> str:
        if r.share_token and base:
            link = f"{base}/share/{r.id}/{r.share_token}"
        elif base:
            link = f"{base}/reports/{r.id}"
        else:
            link = f"/reports/{r.id}"
        title = _xml_escape(r.theme)
        subtitle = _xml_escape(r.subtitle or "")
        pub_at = r.created_at if r.created_at.tzinfo else r.created_at.replace(tzinfo=UTC)
        pub_str = pub_at.strftime("%a, %d %b %Y %H:%M:%S +0000")
        return (
            f"<item>"
            f"<title>{title}</title>"
            f"<link>{_xml_escape(link)}</link>"
            f"<guid isPermaLink=\"false\">forte-report-{r.id}</guid>"
            f"<pubDate>{pub_str}</pubDate>"
            f"<description>{subtitle}</description>"
            f"</item>"
        )

    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0"><channel>'
        '<title>Forte Research</title>'
        f'<link>{_xml_escape(base or "/")}</link>'
        '<description>Reports published by the Forte Research desk.</description>'
        f'<lastBuildDate>{now_str}</lastBuildDate>'
        + "".join(_item(r) for r in rows)
        + '</channel></rss>'
    )
    return Response(content=body, media_type="application/rss+xml")


@router.get("", response_model=list[ReportOut], dependencies=[Depends(require_auth)])
def list_reports(
    include_test: bool = False,
    session: Session = Depends(get_session),
) -> list[ReportOut]:
    """List all reports newest first. By default excludes throwaway
    test-mode runs; pass `include_test=true` to surface them (the
    dashboard's archive page exposes a toggle)."""
    stmt = select(Report)
    if not include_test:
        stmt = stmt.where(Report.is_test == False)  # noqa: E712
    rows = session.exec(stmt.order_by(Report.created_at.desc())).all()
    return [ReportOut.from_db(r) for r in rows]


@router.get("/{report_id}", response_model=ReportOut, dependencies=[Depends(require_auth)])
def get_report(report_id: int, session: Session = Depends(get_session)) -> ReportOut:
    report = session.get(Report, report_id)
    if not report:
        raise HTTPException(404, "Report not found")
    return ReportOut.from_db(report, check_storage=True)


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


class ModeCostStat(BaseModel):
    mode: ReportMode
    n: int
    median_cost_usd: float | None
    p90_cost_usd: float | None


@router.get(
    "/cost_stats",
    response_model=list[ModeCostStat],
    dependencies=[Depends(require_auth)],
)
def cost_stats(session: Session = Depends(get_session)) -> list[ModeCostStat]:
    """Trailing-30-day median + p90 cost per mode, computed off completed
    reports. The /new confirm dialog reads this to ground its estimates
    in actuals -- the static MODE_ESTIMATES dict drifts as prompts grow."""
    cutoff = datetime.now(UTC) - timedelta(days=30)
    rows = session.exec(
        select(Report)
        .where(Report.stage == ReportStage.done)
        .where(Report.created_at >= cutoff)
    ).all()
    by_mode: dict[ReportMode, list[float]] = {}
    for r in rows:
        by_mode.setdefault(r.mode, []).append(r.cost_usd)

    def _percentile(vals: list[float], pct: float) -> float | None:
        if not vals:
            return None
        s = sorted(vals)
        # Nearest-rank percentile -- good enough for a 30-day cohort.
        i = max(0, min(len(s) - 1, int(round(pct * (len(s) - 1)))))
        return s[i]

    out: list[ModeCostStat] = []
    for mode in ReportMode:
        vals = by_mode.get(mode, [])
        out.append(ModeCostStat(
            mode=mode,
            n=len(vals),
            median_cost_usd=_percentile(vals, 0.5),
            p90_cost_usd=_percentile(vals, 0.9),
        ))
    return out


class CostRollup(BaseModel):
    """Sum of audit_log cost across rolling windows. Frontend renders
    today / 7d / 30d on the home page so the user sees the burn at a
    glance instead of having to add up reports manually."""
    today_usd: float
    last_7d_usd: float
    last_30d_usd: float
    n_reports_today: int
    n_reports_7d: int
    n_reports_30d: int


@router.get("/cost_rollup", response_model=CostRollup, dependencies=[Depends(require_auth)])
def cost_rollup(session: Session = Depends(get_session)) -> CostRollup:
    """Rolling cost totals: today, last 7 days, last 30 days. Computed
    off audit_log so it captures every model_call ever made, including
    failed/cancelled report runs that the per-report cost view doesn't
    surface."""
    now = datetime.now(UTC)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_cutoff = now - timedelta(days=7)
    month_cutoff = now - timedelta(days=30)

    rows = session.exec(
        select(AuditLog).where(AuditLog.created_at >= month_cutoff)
    ).all()

    def _aware(d):  # type: ignore[no-untyped-def]
        return d if d.tzinfo else d.replace(tzinfo=UTC)

    cost_day = sum(r.cost_usd for r in rows if _aware(r.created_at) >= start_of_day)
    cost_week = sum(r.cost_usd for r in rows if _aware(r.created_at) >= week_cutoff)
    cost_month = sum(r.cost_usd for r in rows)

    # Report counts on the same windows. Useful divisor: knowing
    # "$5.20 over 6 reports" reads more honestly than "$5.20 today".
    rpt_rows = session.exec(
        select(Report).where(Report.created_at >= month_cutoff)
    ).all()
    rpt_day = sum(1 for r in rpt_rows if _aware(r.created_at) >= start_of_day)
    rpt_week = sum(1 for r in rpt_rows if _aware(r.created_at) >= week_cutoff)
    rpt_month = len(rpt_rows)

    return CostRollup(
        today_usd=round(cost_day, 4),
        last_7d_usd=round(cost_week, 4),
        last_30d_usd=round(cost_month, 4),
        n_reports_today=rpt_day,
        n_reports_7d=rpt_week,
        n_reports_30d=rpt_month,
    )


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
    """Re-queue a report from a specific stage.

    Allowed when the report is failed / cancelled (any stage), or done
    (must specify `from_stage` so you can't accidentally restart a
    finished report from `brief` and burn the whole pipeline). The most
    common done-report use case is re-running `edit` after spotting a
    truncated DISAGREEMENT or running `housekeeping` if it failed
    silently. Workflow stages are idempotent so a re-run overwrites
    that stage's output without disturbing earlier ones."""
    report = session.get(Report, report_id)
    if not report:
        raise HTTPException(404, "Report not found")

    if report.stage in (ReportStage.failed, ReportStage.cancelled):
        # Free resume from anywhere -- the report's broken either way.
        pass
    elif report.stage == ReportStage.done:
        # Must pin a stage. Otherwise hitting Resume on a finished
        # report would re-run from the last queued job (typically
        # `done` itself) which is meaningless, or fall back to `brief`
        # and silently spend $1 redoing everything.
        if from_stage is None:
            raise HTTPException(
                400,
                "Resuming a done report needs ?from_stage=<stage>. "
                "Pick the specific stage you want to re-run "
                "(e.g. edit, render, housekeeping).",
            )
    else:
        raise HTTPException(
            400,
            f"Report is in flight (stage={report.stage}). Cancel or "
            "force-fail it first, then resume.",
        )

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

    # Drop any pending jobs so the new resume_stage job is the only
    # work waiting -- otherwise an old queued `done` job from the prior
    # run could stomp the resumed report back to done.
    pending = session.exec(
        select(Job).where(
            Job.report_id == report_id,
            Job.status == "pending",
        )
    ).all()
    for j in pending:
        j.status = "failed"
        j.last_error = "superseded by /resume"
        j.finished_at = datetime.now(UTC)
        session.add(j)

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


# ----- Public share links -----

class ShareInfo(BaseModel):
    """Admin view of a report's share state. Returned by the auth-gated routes
    so the dashboard can show the URL + offer rotate/revoke."""
    report_id: int
    share_token: str | None
    shared_at: datetime | None


def _share_info(r: Report) -> ShareInfo:
    shared_at = r.shared_at
    if shared_at is not None and shared_at.tzinfo is None:
        shared_at = shared_at.replace(tzinfo=UTC)
    return ShareInfo(report_id=r.id or 0, share_token=r.share_token, shared_at=shared_at)


@router.get("/{report_id}/share", response_model=ShareInfo, dependencies=[Depends(require_auth)])
def get_share(report_id: int, session: Session = Depends(get_session)) -> ShareInfo:
    report = session.get(Report, report_id)
    if not report:
        raise HTTPException(404, "Report not found")
    return _share_info(report)


@router.post("/{report_id}/share", response_model=ShareInfo, dependencies=[Depends(require_auth)])
def create_share(
    report_id: int,
    rotate: bool = False,
    session: Session = Depends(get_session),
) -> ShareInfo:
    """Mint a share token if absent. Pass ?rotate=true to force a fresh one
    (invalidates the existing link)."""
    report = session.get(Report, report_id)
    if not report:
        raise HTTPException(404, "Report not found")
    if report.share_token is None or rotate:
        report.share_token = secrets.token_urlsafe(32)
        report.shared_at = datetime.now(UTC)
        session.add(report)
        session.commit()
        session.refresh(report)
    return _share_info(report)


@router.delete("/{report_id}/share", response_model=ShareInfo, dependencies=[Depends(require_auth)])
def revoke_share(report_id: int, session: Session = Depends(get_session)) -> ShareInfo:
    report = session.get(Report, report_id)
    if not report:
        raise HTTPException(404, "Report not found")
    report.share_token = None
    report.shared_at = None
    session.add(report)
    session.commit()
    session.refresh(report)
    return _share_info(report)


class PublicReport(BaseModel):
    """Subset of Report that's safe to expose unauthenticated. Excludes cost,
    stage, error, audit, jobs -- anything the recipient has no business seeing."""
    id: int
    theme: str
    subtitle: str | None
    contributor_slugs: list[str]
    created_at: datetime
    has_pdf: bool


def _load_shared_report(report_id: int, token: str, session: Session) -> Report:
    """Look up a report by id and verify the token in constant time. Returns
    404 (not 403) on mismatch so an attacker can't enumerate which IDs are
    shared vs unshared."""
    report = session.get(Report, report_id)
    if report is None or not report.share_token:
        raise HTTPException(404, "Not found")
    if not secrets.compare_digest(report.share_token, token):
        raise HTTPException(404, "Not found")
    return report


@router.get("/{report_id}/share/{token}", response_model=PublicReport)
def get_public_report(
    report_id: int,
    token: str,
    session: Session = Depends(get_session),
) -> PublicReport:
    report = _load_shared_report(report_id, token, session)
    created_at = report.created_at if report.created_at.tzinfo else report.created_at.replace(tzinfo=UTC)
    has_pdf = bool(report.pdf_path) or (
        report.stage == ReportStage.done and storage.object_exists(report_id, "report.pdf")
    )
    return PublicReport(
        id=report.id or 0,
        theme=report.theme,
        subtitle=report.subtitle,
        contributor_slugs=list(report.contributor_slugs or []),
        created_at=created_at,
        has_pdf=has_pdf,
    )


@router.get("/{report_id}/share/{token}/pdf")
def get_public_pdf(report_id: int, token: str, session: Session = Depends(get_session)):  # type: ignore[no-untyped-def]
    report = _load_shared_report(report_id, token, session)
    if report.pdf_path:
        path = Path(report.pdf_path)
        if path.exists():
            return FileResponse(
                path,
                media_type="application/pdf",
                filename=path.name,
                headers={
                    "Content-Disposition": f'inline; filename="{path.name}"',
                    "Cache-Control": "public, max-age=3600",
                },
            )
    if report.stage == ReportStage.done and storage.object_exists(report_id, "report.pdf"):
        url = storage.signed_url(report_id, "report.pdf")
        if url:
            return RedirectResponse(url, status_code=302)
    raise HTTPException(404, "PDF not ready")


@router.get("/{report_id}/share/{token}/charts")
def list_public_chart_files(
    report_id: int,
    token: str,
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    _load_shared_report(report_id, token, session)
    from api.workflow.state_machine import working_dir
    wd = working_dir(report_id)
    charts_dir = wd / "charts"
    local_files = sorted(charts_dir.glob("*.png")) if charts_dir.exists() else []
    if local_files:
        return [
            {"filename": p.name, "has_json": p.with_suffix(".json").exists()}
            for p in local_files
        ]
    r2_files = storage.list_chart_files(report_id)
    return [
        {"filename": name, "has_json": storage.object_exists(report_id, "charts", name.replace(".png", ".json"))}
        for name in r2_files
    ]


@router.get("/{report_id}/share/{token}/chart.json")
def get_public_chart_json(
    report_id: int,
    token: str,
    filename: str,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    import json as _json

    _load_shared_report(report_id, token, session)
    from api.workflow.state_machine import working_dir
    wd = working_dir(report_id)
    base = filename.rsplit(".", 1)[0]
    json_path = wd / "charts" / f"{base}.json"
    if not json_path.exists():
        fetched = storage.fetch_to_local(report_id, wd, "charts", f"{base}.json")
        if fetched is None:
            raise HTTPException(404, "Chart sidecar not found")
        json_path = fetched
    payload: dict[str, object] = _json.loads(json_path.read_text(encoding="utf-8"))
    return payload


@router.post("/{report_id}/force_fail", response_model=ReportOut, dependencies=[Depends(require_auth)])
def force_fail(report_id: int, session: Session = Depends(get_session)) -> ReportOut:
    """Force a stuck report into the `failed` state without waiting for the
    watchdog deadline.

    The cancel endpoint marks the report cancelled but leaves the running
    job ticking until the worker notices. force_fail is the triage button
    you reach for when a stage is hung and you want to clean up RIGHT NOW
    so /resume can take over: it marks any running job as failed, drops
    pending jobs, and sets the report stage to failed. After this you can
    call /resume?from_stage=<stage> from the dashboard to re-run the
    stuck stage with a fresh attempt counter."""
    report = session.get(Report, report_id)
    if not report:
        raise HTTPException(404, "Report not found")
    if report.stage in (ReportStage.done, ReportStage.failed, ReportStage.cancelled):
        raise HTTPException(400, f"Report is already {report.stage}")

    now = datetime.now(UTC)
    jobs = session.exec(
        select(Job).where(
            Job.report_id == report_id,
            Job.status.in_(("running", "pending")),  # type: ignore[union-attr]
        )
    ).all()
    for j in jobs:
        j.status = "failed"
        j.last_error = "Force-failed by user"
        if j.finished_at is None:
            j.finished_at = now
        session.add(j)

    report.stage = ReportStage.failed
    report.error = "Force-failed by user"
    session.add(report)
    session.commit()
    session.refresh(report)
    return ReportOut.from_db(report)


@router.post(
    "/{report_id}/rerun_analyst/{slug}",
    response_model=ReportOut,
    dependencies=[Depends(require_auth)],
)
def rerun_analyst(
    report_id: int,
    slug: str,
    session: Session = Depends(get_session),
) -> ReportOut:
    """Re-run a single analyst's draft against their existing notes.

    Triage hook for the parallel-stage timeout case: if one contributor
    in the draft batch hit the per-call timeout, their section reads
    `[draft:slug timed out after Ns]` while everyone else's sections
    landed cleanly. Re-running the whole draft stage works but burns
    tokens on the analysts who were fine. This route re-does just the
    one slot, then queues a render-stage job to refresh the PDF.

    Requires the report to have notes-<slug>.md on disk -- if research
    itself failed for this analyst, /resume from research is the fix."""
    report = session.get(Report, report_id)
    if report is None:
        raise HTTPException(404, "Report not found")

    from api import app_settings
    from api.agents.cost import CostTracker
    from api.workflow.audit import audit_hook
    from api.workflow.state_machine import (
        _make_analyst,
        _models_for,
        _record,
        _resolved_contributors,
        working_dir,
    )

    wd = working_dir(report_id)
    notes_name = f"notes-{slug}.md"
    notes_path = wd / notes_name
    brief_path = wd / "brief.md"

    # Working dir on Railway is ephemeral. Pull missing files back from R2
    # (uploaded at render time) before deciding the re-run is impossible.
    for name in (notes_name, "brief.md"):
        local = wd / name
        if not local.exists() and storage.object_exists(report_id, name):
            storage.fetch_to_local(report_id, wd, name)

    if not notes_path.exists():
        raise HTTPException(
            400,
            f"No notes-{slug}.md on disk or in R2. /resume from research to "
            "rebuild the analyst's notes before re-drafting.",
        )
    if not brief_path.exists():
        raise HTTPException(400, "Brief is missing on disk; can't re-run draft.")

    brief = brief_path.read_text(encoding="utf-8")
    notes = notes_path.read_text(encoding="utf-8")

    # Sanity: confirm the slug is one of the report's known contributors
    # so a typo doesn't let us draft for an arbitrary persona.
    contributors = _resolved_contributors(report, brief)
    if not any(c["slug"] == slug for c in contributors):
        raise HTTPException(
            400,
            f"`{slug}` isn't a contributor on this report. Known: "
            + ", ".join(c["slug"] for c in contributors),
        )

    cap = report.budget_cap_usd or app_settings.cost_per_report_usd()
    cost = CostTracker(
        report_cap=cap,
        day_cap=app_settings.cost_per_day_usd(),
    )
    audit = audit_hook(report_id)
    models = _models_for(report.mode)

    analyst = _make_analyst(slug, cost, audit, models["analyst"])
    result = analyst.draft(brief, notes, report.theme)
    if not result.text.strip():
        raise HTTPException(500, "Draft re-run produced empty output")
    (wd / f"section-{slug}.md").write_text(result.text, encoding="utf-8")
    _record(report_id, wd, result)

    # Re-render so the PDF picks up the new section. We push the report
    # back to render, drop any pending non-render jobs (they'd have run
    # against the old section), and queue a fresh render job.
    report.stage = ReportStage.render
    report.error = None
    session.add(report)

    pending = session.exec(
        select(Job).where(
            Job.report_id == report_id,
            Job.status == "pending",
        )
    ).all()
    for j in pending:
        j.status = "failed"
        j.last_error = "superseded by single-analyst re-run"
        j.finished_at = datetime.now(UTC)
        session.add(j)

    session.add(Job(report_id=report_id, stage=ReportStage.render))
    session.commit()
    session.refresh(report)
    return ReportOut.from_db(report)


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


# ----- Uploaded research notes / spreadsheets -----

ALLOWED_UPLOAD_SUFFIXES = {".pdf", ".csv", ".tsv", ".xlsx", ".xlsm", ".md", ".markdown", ".txt"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10MB; bigger and the agent's context budget won't hold it


class UploadedDocOut(BaseModel):
    id: int
    report_id: int
    filename: str
    mime: str
    size_bytes: int
    summary: str
    created_at: datetime

    @classmethod
    def from_db(cls, d: UploadedDocument) -> UploadedDocOut:
        created_at = d.created_at if d.created_at.tzinfo else d.created_at.replace(tzinfo=UTC)
        return cls(
            id=d.id or 0,
            report_id=d.report_id,
            filename=d.filename,
            mime=d.mime,
            size_bytes=d.size_bytes,
            summary=d.summary,
            created_at=created_at,
        )


@router.get(
    "/{report_id}/uploads",
    response_model=list[UploadedDocOut],
    dependencies=[Depends(require_auth)],
)
def list_uploads(report_id: int, session: Session = Depends(get_session)) -> list[UploadedDocOut]:
    if not session.get(Report, report_id):
        raise HTTPException(404, "Report not found")
    return [UploadedDocOut.from_db(d) for d in uploads.list_documents(report_id)]


@router.post(
    "/{report_id}/uploads",
    response_model=UploadedDocOut,
    dependencies=[Depends(require_auth)],
)
async def upload_document(
    report_id: int,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
) -> UploadedDocOut:
    """Attach a research note or CSV to a report. The file is text-extracted at
    upload time; the analyst agents see it via the `uploaded_documents` tool."""
    if not session.get(Report, report_id):
        raise HTTPException(404, "Report not found")

    filename = (file.filename or "upload.bin").replace("/", "_").replace("\\", "_")
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix not in ALLOWED_UPLOAD_SUFFIXES:
        raise HTTPException(400, f"File type not supported: {suffix or '(none)'}. Allowed: {sorted(ALLOWED_UPLOAD_SUFFIXES)}")

    data = await file.read()
    if len(data) == 0:
        raise HTTPException(400, "Empty upload")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, f"File too large ({len(data)} bytes; max {MAX_UPLOAD_BYTES})")

    mime = file.content_type or "application/octet-stream"
    doc = uploads.save_upload(report_id, filename, mime, data)
    return UploadedDocOut.from_db(doc)


@router.delete(
    "/{report_id}/uploads/{filename}",
    dependencies=[Depends(require_auth)],
)
def remove_upload(
    report_id: int,
    filename: str,
    session: Session = Depends(get_session),
) -> dict[str, bool]:
    if not session.get(Report, report_id):
        raise HTTPException(404, "Report not found")
    ok = uploads.delete_document(report_id, filename)
    if not ok:
        raise HTTPException(404, "Upload not found")
    return {"deleted": True}


# ----- Reading mode: HTML rendering of the report content -----

class ReadingSection(BaseModel):
    heading: str
    body_html: str
    author: str | None = None
    role: str | None = None


class ReadingSource(BaseModel):
    n: int
    url: str
    title: str | None
    source: str


class ReadingMode(BaseModel):
    """Structured representation of a finished report for the web reader.
    Sections are pre-rendered to HTML (markdown-it + inline citations + chart
    `<figure>` blocks rewritten to API URLs the browser can hit)."""
    id: int
    theme: str
    subtitle: str | None
    contributors: list[dict[str, str]]
    house_view_top: str | None
    house_view_bottom: str | None
    sections: list[ReadingSection]
    glossary_html: str | None = None
    sources: list[ReadingSource]
    created_at: datetime


def _build_reading_payload(report: Report, *, public_token: str | None = None) -> ReadingMode:
    """Reconstruct the report from its working-dir files. Used by both the
    auth-gated reading endpoint and the public share variant."""
    if report.id is None:
        raise HTTPException(500, "Report has no id")
    # Reading mode just needs the post-edit prose + sources -- both land
    # at the `render` stage. After that, feedback / housekeeping run but
    # they don't touch the rendered content. Allow any stage from render
    # onwards so a report whose feedback or housekeeping stage failed can
    # still be read while we triage.
    _readable_stages = {
        ReportStage.render, ReportStage.feedback,
        ReportStage.housekeeping, ReportStage.done,
    }
    if report.stage not in _readable_stages:
        raise HTTPException(400, f"Report is not finished (stage={report.stage})")

    import re as _re

    from markdown_it import MarkdownIt

    from api.citations import attach_inline_citations
    from api.workflow.state_machine import (
        DC_DISPLAY,
        EIC_DISPLAY,
        _read_sources,
        _resolved_contributors,
        _sections_for_render,
        parse_edited,
        working_dir,
    )

    wd = working_dir(report.id)

    # Working dir on Railway is ephemeral; markdown sources may be gone after a
    # rebuild even though the report is `done`. Pull them back from R2 when
    # they're missing locally so reading mode keeps working across rebuilds.
    for name in ("edited.md", "brief.md", "data-section.md", "sources.json"):
        if not (wd / name).exists() and storage.object_exists(report.id, name):
            storage.fetch_to_local(report.id, wd, name)

    edited_path = wd / "edited.md"
    brief_path = wd / "brief.md"
    if not edited_path.exists():
        raise HTTPException(404, "Report content not on disk (working dir was wiped and not on R2)")

    edited = edited_path.read_text(encoding="utf-8")
    brief = brief_path.read_text(encoding="utf-8") if brief_path.exists() else ""

    # Backfill: if the markdown is local but not yet on R2, push it now so the
    # next rebuild can find it. Best-effort and silent.
    try:
        if storage.is_r2_enabled() and not storage.object_exists(report.id, "edited.md"):
            storage.upload_artifacts(report.id, wd)
    except Exception:  # noqa: BLE001
        pass

    parsed = parse_edited(edited)

    raw_sections = _sections_for_render(parsed, wd)
    cited_sections, ordered_sources = attach_inline_citations(
        raw_sections, _read_sources(wd), check_urls=False,  # already filtered at PDF render time
    )

    # Rewrite `<figure><img src="file://...charts/foo.png">` references so the
    # browser can fetch the chart through the API (or share endpoint) instead.
    if public_token is not None:
        chart_url_base = f"/reports/{report.id}/share/{public_token}/chart-image"
    else:
        chart_url_base = f"/reports/{report.id}/chart-image"

    md = MarkdownIt("commonmark", {"html": True}).enable("table")
    file_uri_re = _re.compile(r'src="file://[^"]*?/charts/([^"]+)"')

    def render_body(body_md: str) -> str:
        rewritten = file_uri_re.sub(
            lambda m: f'src="{chart_url_base}?filename={m.group(1)}"',
            body_md,
        )
        return md.render(rewritten)

    sections = [
        ReadingSection(
            heading=s.heading,
            body_html=render_body(s.body_md),
            author=s.author,
            role=s.role,
        )
        for s in cited_sections
    ]

    # Glossary appendix lives in its own slot at the end of the read --
    # rendered after the bottom-line callout, before sources -- so it
    # doesn't get sandwiched between contributor sections.
    glossary_path = wd / "glossary.md"
    glossary_html: str | None = None
    if glossary_path.exists():
        from api.agents.glossary import is_empty
        gl_text = glossary_path.read_text(encoding="utf-8")
        if not is_empty(gl_text):
            body = gl_text
            for line in gl_text.splitlines():
                if line.lstrip().startswith("# "):
                    after = gl_text.split(line, 1)[1]
                    body = after.lstrip("\n")
                    break
            glossary_html = md.render(body)

    contributors = [
        {"name": EIC_DISPLAY["name"], "role": EIC_DISPLAY["role"]},
        *[{"name": c["name"], "role": c["role"]} for c in _resolved_contributors(report, brief)],
        {"name": DC_DISPLAY["name"], "role": DC_DISPLAY["role"]},
    ]

    created_at = report.created_at if report.created_at.tzinfo else report.created_at.replace(tzinfo=UTC)
    return ReadingMode(
        id=report.id,
        theme=report.theme,
        subtitle=report.subtitle,
        contributors=contributors,
        house_view_top=str(parsed.get("house_view_top") or "") or None,
        house_view_bottom=str(parsed.get("house_view_bottom") or "") or None,
        sections=sections,
        glossary_html=glossary_html,
        sources=[
            ReadingSource(
                n=int(s["n"] or "0"),
                url=str(s["url"] or ""),
                title=s["title"],
                source=str(s["source"] or "web"),
            )
            for s in ordered_sources
        ],
        created_at=created_at,
    )


@router.get("/{report_id}/reading", response_model=ReadingMode, dependencies=[Depends(require_auth)])
def get_reading(report_id: int, session: Session = Depends(get_session)) -> ReadingMode:
    """HTML-rendered version of the report -- alternative to the embedded PDF
    that's much nicer on mobile."""
    report = session.get(Report, report_id)
    if not report:
        raise HTTPException(404, "Report not found")
    return _build_reading_payload(report)


@router.get("/{report_id}/share/{token}/reading", response_model=ReadingMode)
def get_public_reading(
    report_id: int,
    token: str,
    session: Session = Depends(get_session),
) -> ReadingMode:
    report = _load_shared_report(report_id, token, session)
    return _build_reading_payload(report, public_token=token)


@router.get("/{report_id}/chart-image", dependencies=[Depends(require_auth)])
def get_chart_image(report_id: int, filename: str, session: Session = Depends(get_session)):  # type: ignore[no-untyped-def]
    """Serve a chart PNG by filename. Used by reading mode's <img> tags."""
    return _serve_chart_image(report_id, filename, session)


@router.get("/{report_id}/share/{token}/chart-image")
def get_public_chart_image(report_id: int, token: str, filename: str, session: Session = Depends(get_session)):  # type: ignore[no-untyped-def]
    _load_shared_report(report_id, token, session)
    return _serve_chart_image(report_id, filename, session)


def _serve_chart_image(report_id: int, filename: str, session: Session):  # type: ignore[no-untyped-def]
    if session.get(Report, report_id) is None:
        raise HTTPException(404, "Report not found")
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "Invalid filename")
    from api.workflow.state_machine import working_dir
    wd = working_dir(report_id)
    local = wd / "charts" / filename
    if local.exists():
        return FileResponse(local, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})
    if storage.object_exists(report_id, "charts", filename):
        url = storage.signed_url(report_id, "charts", filename)
        if url:
            return RedirectResponse(url, status_code=302)
    raise HTTPException(404, "Chart not found")


# ----- Auto-thread (5-tweet distillation persisted by housekeeping) -----

class ThreadOut(BaseModel):
    text: str | None  # None when housekeeping hasn't run / failed


@router.get("/{report_id}/thread", response_model=ThreadOut, dependencies=[Depends(require_auth)])
def get_thread(report_id: int, session: Session = Depends(get_session)) -> ThreadOut:
    if session.get(Report, report_id) is None:
        raise HTTPException(404, "Report not found")
    from api.workflow.state_machine import working_dir
    p = working_dir(report_id) / "thread.md"
    if not p.exists():
        return ThreadOut(text=None)
    return ThreadOut(text=p.read_text(encoding="utf-8"))


# ----- Ask-the-analyst (post-publish chat with a contributing persona) -----

class AskRequest(BaseModel):
    persona_slug: str
    message: str
    history: list[dict[str, str]] = []  # [{role: "user"|"assistant", content: ...}]


class AskResponse(BaseModel):
    reply: str
    cost_usd: float


@router.post("/{report_id}/ask", response_model=AskResponse, dependencies=[Depends(require_auth)])
def ask_analyst(
    report_id: int,
    payload: AskRequest,
    session: Session = Depends(get_session),
) -> AskResponse:
    """Chat with a contributing persona about a published report. The persona
    file is the system prompt; the report markdown is loaded as context."""
    from api import app_settings
    from api.agents.conversation import Conversation
    from api.agents.cost import CostTracker
    from api.workflow.state_machine import _persona_path, working_dir

    report = session.get(Report, report_id)
    if not report:
        raise HTTPException(404, "Report not found")
    if report.stage != ReportStage.done:
        raise HTTPException(400, "Report isn't published yet")

    persona_path = _persona_path(payload.persona_slug)
    if persona_path is None:
        raise HTTPException(404, f"Unknown persona: {payload.persona_slug}")

    # Use the audited prose if present (it's what the reader saw), fall back
    # to edited.md, fall back to a tiny note about the report theme.
    wd = working_dir(report_id)
    body = ""
    for fn in ("audited.md", "edited.md"):
        p = wd / fn
        if p.exists() and p.read_text(encoding="utf-8").strip():
            body = p.read_text(encoding="utf-8")
            break
    if not body:
        body = f"(report body not available locally; theme: {report.theme})"

    cost = CostTracker(
        report_cap=app_settings.cost_per_report_usd(),
        day_cap=app_settings.cost_per_day_usd(),
    )
    conv = Conversation(persona_path=persona_path, cost=cost)
    result = conv.reply(
        report_theme=report.theme,
        report_markdown=body,
        history=payload.history,
        user_message=payload.message,
    )
    return AskResponse(reply=result.text, cost_usd=result.cost_usd)


# ----- Position tracker (current open + closed across all reports) -----

class PositionRow(BaseModel):
    id: int
    report_id: int
    asset: str
    direction: str
    horizon_days: int
    target_level: float | None
    conviction: int
    contributor_slug: str
    claim_text: str
    made_at: datetime
    price_at_call: float | None
    evaluated_at: datetime | None
    price_at_evaluation: float | None
    price_current: float | None = None
    outcome: str | None


@router.get("/positions/open", response_model=list[PositionRow], dependencies=[Depends(require_auth)])
def list_open_positions() -> list[PositionRow]:
    """Calls that haven't matured yet -- the firm's current stance."""
    from api import calls as calls_mod
    rows = calls_mod.open_positions()
    # Live spot for unmatured calls so the dashboard can show winners/losers.
    # _spot_price hits a 1h-cached yfinance fetch.
    current_by_asset: dict[str, float | None] = {}
    for r in rows:
        if r.asset not in current_by_asset:
            try:
                current_by_asset[r.asset] = calls_mod._spot_price(r.asset)
            except Exception:  # noqa: BLE001
                current_by_asset[r.asset] = None
    return [_to_position_row(r, price_current=current_by_asset.get(r.asset)) for r in rows]


@router.get("/positions/closed", response_model=list[PositionRow], dependencies=[Depends(require_auth)])
def list_closed_positions() -> list[PositionRow]:
    """Resolved calls, most-recent first."""
    from api import calls as calls_mod
    rows = calls_mod.graded_history()
    return [_to_position_row(r) for r in rows]


def _to_position_row(c, *, price_current: float | None = None) -> PositionRow:  # type: ignore[no-untyped-def]
    return PositionRow(
        id=c.id or 0,
        report_id=c.report_id,
        asset=c.asset,
        direction=c.direction.value if hasattr(c.direction, "value") else str(c.direction),
        horizon_days=c.horizon_days,
        target_level=c.target_level,
        conviction=c.conviction,
        contributor_slug=c.contributor_slug,
        claim_text=c.claim_text,
        made_at=c.made_at,
        price_at_call=c.price_at_call,
        evaluated_at=c.evaluated_at,
        price_at_evaluation=c.price_at_evaluation,
        price_current=price_current,
        outcome=c.outcome.value if c.outcome and hasattr(c.outcome, "value") else None,
    )


# ----- Email a finished report -----

class EmailReportRequest(BaseModel):
    to: str
    note: str = ""            # optional cover note from the sender
    include_pdf: bool = True  # attach the PDF (when generated + small enough)


class EmailReportResponse(BaseModel):
    sent: bool
    detail: str = ""


@router.post(
    "/{report_id}/email",
    response_model=EmailReportResponse,
    dependencies=[Depends(require_auth)],
)
def email_report(
    report_id: int,
    payload: EmailReportRequest,
    session: Session = Depends(get_session),
) -> EmailReportResponse:
    """Send the report to a recipient. Mints a public share token if there
    isn't one already so the email links don't require dashboard auth."""
    report = session.get(Report, report_id)
    if not report:
        raise HTTPException(404, "Report not found")
    if report.stage != ReportStage.done:
        raise HTTPException(400, f"Report is not finished (stage={report.stage})")

    if not report.share_token:
        report.share_token = secrets.token_urlsafe(32)
        report.shared_at = datetime.now(UTC)
        session.add(report)
        session.commit()
        session.refresh(report)

    pdf_bytes: bytes | None = None
    if payload.include_pdf and report.pdf_path:
        try:
            data = Path(report.pdf_path).read_bytes()
            if len(data) <= 25 * 1024 * 1024:  # Resend caps attachments around 40MB; stay safe
                pdf_bytes = data
        except OSError:
            pdf_bytes = None

    from api.email import send_report

    ok, detail = send_report(
        to=payload.to,
        report=report,
        share_token=report.share_token or "",
        cover_note=payload.note,
        pdf_bytes=pdf_bytes,
    )
    return EmailReportResponse(sent=ok, detail=detail)


# ----- Model breakdown -----

class ModelBreakdownRow(BaseModel):
    model: str
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


@router.get(
    "/{report_id}/model_breakdown",
    response_model=list[ModelBreakdownRow],
    dependencies=[Depends(require_auth)],
)
def model_breakdown(report_id: int, session: Session = Depends(get_session)) -> list[ModelBreakdownRow]:
    """Aggregate per-model token usage + cost for one report. Reads from the
    audit log (every model_call event carries model + token counts + cost)."""
    if not session.get(Report, report_id):
        raise HTTPException(404, "Report not found")
    rows = session.exec(
        select(AuditLog)
        .where(AuditLog.report_id == report_id)
        .where(AuditLog.event == "model_call")
    ).all()
    bucket: dict[str, dict[str, float]] = {}
    for r in rows:
        details = dict(r.details or {})
        model = str(details.get("model") or "unknown")
        b = bucket.setdefault(model, {"calls": 0.0, "input": 0.0, "output": 0.0, "cost": 0.0})
        b["calls"] += 1
        b["input"] += float(details.get("input_tokens") or 0)
        b["output"] += float(details.get("output_tokens") or 0)
        b["cost"] += float(r.cost_usd or 0.0)
    return [
        ModelBreakdownRow(
            model=m,
            calls=int(b["calls"]),
            input_tokens=int(b["input"]),
            output_tokens=int(b["output"]),
            cost_usd=round(b["cost"], 4),
        )
        for m, b in sorted(bucket.items(), key=lambda kv: -kv[1]["cost"])
    ]
