"""Rules-based hire/fire review.

Run periodically (or on demand from the dashboard) to surface pending
Recommendations. Approval/dismissal is handled by routes/recruiter.py."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, select

from api.db import engine
from api.models import (
    Recommendation,
    RecommendationKind,
    RecommendationStatus,
    Report,
    ReportStage,
)
from api.settings import settings
from api.workflow.state_machine import _persona_meta, get_roster

log = logging.getLogger("recruiter.review")

STALE_DAYS = 30


def refresh_recommendations() -> int:
    """Run all rules; insert new pending Recommendations (deduped). Returns the count added."""
    added = 0
    with Session(engine) as session:
        added += _propose_promotions(session)
        added += _propose_fires_for_stale(session)
        added += _propose_fires_for_underperformance(session)
        session.commit()
    log.info("recruiter review added %d new recommendations", added)
    return added


def _has_open_rec(session: Session, slug: str, kind: RecommendationKind) -> bool:
    """True if a pending or approved rec already exists for this slug+kind."""
    row = session.exec(
        select(Recommendation).where(
            Recommendation.subject_slug == slug,
            Recommendation.kind == kind,
            Recommendation.status != RecommendationStatus.dismissed,
        )
    ).first()
    return row is not None


def _propose_promotions(session: Session) -> int:
    """Suggest promoting a temp persona that contributed to at least one done report."""
    temp_dir = settings.team_dir / "temp"
    if not temp_dir.exists():
        return 0
    added = 0
    done_reports = session.exec(
        select(Report)
        .where(Report.stage == ReportStage.done)
        .where(Report.is_test == False)  # noqa: E712 -- test runs don't count as real work
    ).all()

    for path in temp_dir.glob("*.md"):
        slug = path.stem
        if not any(slug in (r.contributor_slugs or []) for r in done_reports):
            continue
        if _has_open_rec(session, slug, RecommendationKind.promote):
            continue
        meta = _persona_meta(slug)
        if not meta:
            continue
        contributions = sum(1 for r in done_reports if slug in (r.contributor_slugs or []))
        session.add(Recommendation(
            kind=RecommendationKind.promote,
            subject_slug=slug,
            subject_name=meta["name"],
            subject_role=meta["role"],
            reasoning=(
                f"Contributed to {contributions} report"
                f"{'s' if contributions != 1 else ''} as a temp specialist. "
                f"Recommend promotion to standing roster."
            ),
        ))
        added += 1
    return added


UNDERPERFORM_MIN_GRADED = 5
UNDERPERFORM_HIT_RATE = 0.30


def _propose_fires_for_underperformance(session: Session) -> int:
    """Suggest firing analysts whose graded performance ledger sits below
    UNDERPERFORM_HIT_RATE with at least UNDERPERFORM_MIN_GRADED resolved
    calls. The hit rate uses (hits + 0.5 * partials) / graded -- same as
    the team page exposes."""
    from api import calls as calls_mod
    rates = calls_mod.hit_rate_by_persona()
    if not rates:
        return 0
    added = 0
    for member in get_roster():
        slug = member["slug"]
        if _has_open_rec(session, slug, RecommendationKind.fire):
            continue
        agg = rates.get(slug)
        if not agg:
            continue
        graded = int(agg.get("graded", 0))
        rate = float(agg.get("hit_rate", 0.0))
        if graded < UNDERPERFORM_MIN_GRADED or rate >= UNDERPERFORM_HIT_RATE:
            continue
        session.add(Recommendation(
            kind=RecommendationKind.fire,
            subject_slug=slug,
            subject_name=member["name"],
            subject_role=member["role"],
            reasoning=(
                f"Hit rate of {rate:.0%} across {graded} graded calls is "
                f"below the {UNDERPERFORM_HIT_RATE:.0%} threshold. "
                f"Recommend firing -- archive the persona; rehire later if "
                f"the strategy changes."
            ),
        ))
        added += 1
    return added


def _propose_fires_for_stale(session: Session) -> int:
    """Suggest firing standing analysts with no contributions in STALE_DAYS days."""
    cutoff = datetime.now(UTC) - timedelta(days=STALE_DAYS)
    added = 0
    rows = session.exec(select(Report)).all()

    for member in get_roster():
        slug = member["slug"]
        if _has_open_rec(session, slug, RecommendationKind.fire):
            continue
        contributing = [r for r in rows if slug in (r.contributor_slugs or [])]
        if not contributing:
            # Never used. Don't fire on day one -- give it a grace period.
            # (Could implement a separate "never assigned" rule later.)
            continue
        last = max(r.created_at for r in contributing)
        # Normalise tz for comparison.
        last_aware = last if last.tzinfo else last.replace(tzinfo=UTC)
        if last_aware >= cutoff:
            continue
        session.add(Recommendation(
            kind=RecommendationKind.fire,
            subject_slug=slug,
            subject_name=member["name"],
            subject_role=member["role"],
            reasoning=(
                f"No assignments in {STALE_DAYS}+ days "
                f"(last contribution {last_aware.date().isoformat()}). "
                f"Recommend firing -- archive the persona; it can be rehired later."
            ),
        ))
        added += 1
    return added
