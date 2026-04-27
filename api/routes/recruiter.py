"""Recruiter endpoints: review the team, surface recs, approve/dismiss."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from api.auth import require_auth
from api.db import get_session
from api.models import Recommendation, RecommendationKind, RecommendationStatus
from api.recruiter_review import refresh_recommendations
from api.settings import settings

router = APIRouter(prefix="/recruiter", tags=["recruiter"])


class RecommendationOut(BaseModel):
    id: int
    kind: RecommendationKind
    subject_slug: str
    subject_name: str
    subject_role: str
    reasoning: str
    status: RecommendationStatus
    created_at: datetime
    resolved_at: datetime | None

    @classmethod
    def from_db(cls, r: Recommendation) -> RecommendationOut:
        return cls(
            id=r.id or 0,
            kind=r.kind,
            subject_slug=r.subject_slug,
            subject_name=r.subject_name,
            subject_role=r.subject_role,
            reasoning=r.reasoning,
            status=r.status,
            created_at=r.created_at,
            resolved_at=r.resolved_at,
        )


@router.post("/review", dependencies=[Depends(require_auth)])
def kick_review() -> dict[str, int]:
    """Run the rules-based review and surface any new recommendations."""
    n = refresh_recommendations()
    return {"added": n}


@router.get("/recommendations", response_model=list[RecommendationOut], dependencies=[Depends(require_auth)])
def list_recs(
    status: RecommendationStatus | None = None,
    session: Session = Depends(get_session),
) -> list[RecommendationOut]:
    q = select(Recommendation).order_by(Recommendation.created_at.desc())  # type: ignore[attr-defined]
    if status is not None:
        q = q.where(Recommendation.status == status)
    return [RecommendationOut.from_db(r) for r in session.exec(q).all()]


@router.post(
    "/recommendations/{rec_id}/approve",
    response_model=RecommendationOut,
    dependencies=[Depends(require_auth)],
)
def approve(rec_id: int, session: Session = Depends(get_session)) -> RecommendationOut:
    rec = session.get(Recommendation, rec_id)
    if not rec:
        raise HTTPException(404, "Recommendation not found")
    if rec.status != RecommendationStatus.pending:
        raise HTTPException(400, f"Recommendation is already {rec.status}")

    if rec.kind == RecommendationKind.promote:
        src = settings.team_dir / "temp" / f"{rec.subject_slug}.md"
        dst = settings.team_dir / f"{rec.subject_slug}.md"
        if not src.exists():
            raise HTTPException(409, f"Temp persona missing: {src.name}")
        if dst.exists():
            raise HTTPException(409, f"Standing persona already exists: {dst.name}")
        src.rename(dst)
    elif rec.kind == RecommendationKind.fire:
        src = settings.team_dir / f"{rec.subject_slug}.md"
        archive_dir = settings.team_dir / "archive"
        archive_dir.mkdir(exist_ok=True)
        dst = archive_dir / f"{rec.subject_slug}.md"
        if not src.exists():
            raise HTTPException(409, f"Standing persona missing: {src.name}")
        # If archive already has this slug (re-fired after rehire), suffix with timestamp.
        if dst.exists():
            ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            dst = archive_dir / f"{rec.subject_slug}-{ts}.md"
        src.rename(dst)

    rec.status = RecommendationStatus.approved
    rec.resolved_at = datetime.now(UTC)
    session.add(rec)
    session.commit()
    session.refresh(rec)
    return RecommendationOut.from_db(rec)


@router.post(
    "/recommendations/{rec_id}/dismiss",
    response_model=RecommendationOut,
    dependencies=[Depends(require_auth)],
)
def dismiss(rec_id: int, session: Session = Depends(get_session)) -> RecommendationOut:
    rec = session.get(Recommendation, rec_id)
    if not rec:
        raise HTTPException(404, "Recommendation not found")
    if rec.status != RecommendationStatus.pending:
        raise HTTPException(400, f"Recommendation is already {rec.status}")
    rec.status = RecommendationStatus.dismissed
    rec.resolved_at = datetime.now(UTC)
    session.add(rec)
    session.commit()
    session.refresh(rec)
    return RecommendationOut.from_db(rec)
