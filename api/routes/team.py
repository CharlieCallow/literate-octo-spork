"""Team roster + persona detail/edit endpoints."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from api.auth import require_auth
from api.db import get_session
from api.models import Report, ReportStage
from api.settings import settings
from api.workflow.state_machine import get_roster, get_roster_map

router = APIRouter(prefix="/team", tags=["team"])


class Member(BaseModel):
    slug: str
    name: str
    role: str
    reports_contributed: int
    last_assignment_at: datetime | None
    rewrite_ratio: float | None  # placeholder for M4 sprint 4 — None for now
    # Performance ledger / calibration
    calls_total: int = 0
    calls_graded: int = 0
    hit_rate: float | None = None
    avg_conviction: float | None = None
    # Voice drift (axis name + signed pct gap to firm mean), null when no drift
    drift_axis: str | None = None
    drift_pct: float | None = None


class PersonaOut(BaseModel):
    slug: str
    name: str
    role: str
    markdown: str
    reports_contributed: int
    last_assignment_at: datetime | None


class PersonaUpdate(BaseModel):
    markdown: str


def _stats_for(slug: str, session: Session) -> tuple[int, datetime | None]:
    rows = session.exec(
        select(Report).where(Report.stage != ReportStage.failed)  # type: ignore[arg-type]
    ).all()
    contributed = [r for r in rows if slug in (r.contributor_slugs or [])]
    last = max((r.created_at for r in contributed), default=None)
    return len(contributed), last


@router.get("", response_model=list[Member], dependencies=[Depends(require_auth)])
def list_team(session: Session = Depends(get_session)) -> list[Member]:
    from api import calibration as cal_mod
    from api import voice_stats as voice_mod

    cal_by_slug = cal_mod.for_all()
    drift_alerts = {a.persona_slug: a for a in voice_mod.detect_drift()}

    out: list[Member] = []
    for m in get_roster():
        slug = m["slug"]
        n, last = _stats_for(slug, session)
        cal = cal_by_slug.get(slug)
        drift = drift_alerts.get(slug)
        out.append(Member(
            slug=slug, name=m["name"], role=m["role"],
            reports_contributed=n, last_assignment_at=last, rewrite_ratio=None,
            calls_total=cal.n_total if cal else 0,
            calls_graded=cal.n_graded if cal else 0,
            hit_rate=cal.hit_rate if cal else None,
            avg_conviction=cal.avg_conviction if cal else None,
            drift_axis=drift.axis if drift else None,
            drift_pct=drift.pct_drift if drift else None,
        ))
    return out


@router.get("/{slug}", response_model=PersonaOut, dependencies=[Depends(require_auth)])
def get_persona(slug: str, session: Session = Depends(get_session)) -> PersonaOut:
    if slug not in get_roster_map():
        raise HTTPException(404, f"Unknown contributor: {slug}")
    persona_path = settings.team_dir / f"{slug}.md"
    if not persona_path.exists():
        raise HTTPException(404, f"Persona file missing: {persona_path.name}")
    n, last = _stats_for(slug, session)
    m = get_roster_map()[slug]
    return PersonaOut(
        slug=slug,
        name=m["name"],
        role=m["role"],
        markdown=persona_path.read_text(encoding="utf-8"),
        reports_contributed=n,
        last_assignment_at=last,
    )


class ArchivedMember(BaseModel):
    slug: str
    name: str
    role: str


class HouseViewOut(BaseModel):
    markdown: str
    updated_at: datetime | None
    last_report_id: int | None


@router.get("/house-view", response_model=HouseViewOut, dependencies=[Depends(require_auth)])
def get_house_view() -> HouseViewOut:
    """Current rolling house view. Updated at the end of every report."""
    from api import house_view as house_view_mod
    from api.db import engine
    from api.models import HouseView
    with Session(engine) as session:
        row = session.get(HouseView, 1)
    return HouseViewOut(
        markdown=house_view_mod.get(),
        updated_at=row.updated_at if row else None,
        last_report_id=row.last_report_id if row else None,
    )


@router.get("/archive", response_model=list[ArchivedMember], dependencies=[Depends(require_auth)])
def list_archive() -> list[ArchivedMember]:
    """List archived (fired) personas. Each can be rehired via /team/{slug}/rehire."""
    from api import personas as personas_module
    from api.models import PersonaStatus
    return [
        ArchivedMember(slug=p.slug, name=p.name or p.slug, role=p.role or "Analyst")
        for p in personas_module.list_by_status(PersonaStatus.archived)
    ]


@router.post("/{slug}/rehire", response_model=PersonaOut, dependencies=[Depends(require_auth)])
def rehire(slug: str, session: Session = Depends(get_session)) -> PersonaOut:
    """Move an archived persona back to the standing roster."""
    from api import personas
    from api.models import PersonaStatus
    p = personas.get(slug)
    if not p:
        raise HTTPException(404, f"Unknown persona: {slug}")
    if p.status != PersonaStatus.archived:
        raise HTTPException(400, f"Persona is not archived (status={p.status})")
    personas.set_status(slug, PersonaStatus.standing)
    return get_persona(slug, session)


@router.put("/{slug}", response_model=PersonaOut, dependencies=[Depends(require_auth)])
def update_persona(
    slug: str,
    payload: PersonaUpdate,
    session: Session = Depends(get_session),
) -> PersonaOut:
    if slug not in get_roster_map():
        raise HTTPException(404, f"Unknown contributor: {slug}")
    if not payload.markdown.strip():
        raise HTTPException(400, "Persona markdown cannot be empty")
    if not payload.markdown.lstrip().startswith("# "):
        raise HTTPException(400, "Persona must start with a top-level heading (# Name)")
    from api import personas
    personas.upsert(slug, payload.markdown)
    return get_persona(slug, session)
