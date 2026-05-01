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

    from api import personas
    from api.models import PersonaStatus

    if rec.kind == RecommendationKind.promote:
        p = personas.get(rec.subject_slug)
        if p is None:
            raise HTTPException(409, f"Temp persona missing in DB: {rec.subject_slug}")
        personas.set_status(rec.subject_slug, PersonaStatus.standing)
    elif rec.kind == RecommendationKind.fire:
        p = personas.get(rec.subject_slug)
        if p is None:
            raise HTTPException(409, f"Standing persona missing in DB: {rec.subject_slug}")
        personas.set_status(rec.subject_slug, PersonaStatus.archived)
        # Reset the failure ledger for this agent. Approving a fire archives
        # them out of the roster anyway, but keeping unresolved rows around
        # would let stray queries still treat them as broken.
        from api import report_failures
        report_failures.mark_resolved_for(agent=rec.subject_slug)

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
    if rec.kind == RecommendationKind.fire:
        # Dismissal = "false alarm, leave them on the team". Reset the
        # failure ledger so the EIC's brief stops blacklisting them and
        # the same rule doesn't immediately re-fire on the next review.
        from api import report_failures
        report_failures.mark_resolved_for(agent=rec.subject_slug)
    rec.status = RecommendationStatus.dismissed
    rec.resolved_at = datetime.now(UTC)
    session.add(rec)
    session.commit()
    session.refresh(rec)
    return RecommendationOut.from_db(rec)
