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
    out: list[Member] = []
    for m in get_roster():
        n, last = _stats_for(m["slug"], session)
        out.append(Member(
            slug=m["slug"], name=m["name"], role=m["role"],
            reports_contributed=n, last_assignment_at=last, rewrite_ratio=None,
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


@router.put("/{slug}", response_model=PersonaOut, dependencies=[Depends(require_auth)])
def update_persona(
    slug: str,
    payload: PersonaUpdate,
    session: Session = Depends(get_session),
) -> PersonaOut:
    if slug not in get_roster_map():
        raise HTTPException(404, f"Unknown contributor: {slug}")
    persona_path = settings.team_dir / f"{slug}.md"
    if not persona_path.exists():
        raise HTTPException(404, f"Persona file missing: {persona_path.name}")
    if not payload.markdown.strip():
        raise HTTPException(400, "Persona markdown cannot be empty")
    if not payload.markdown.lstrip().startswith("# "):
        raise HTTPException(400, "Persona must start with a top-level heading (# Name)")
    persona_path.write_text(payload.markdown, encoding="utf-8")
    return get_persona(slug, session)
