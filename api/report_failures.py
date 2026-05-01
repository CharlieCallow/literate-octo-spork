"""Failure-mode memory across reports.

`section_audit` (and any other audit-stage gate that can blame a contributor)
writes a `ReportFailure` row each time an agent fails. The recruiter review
turns 3+ unresolved exclusions at the same stage into a fire/replace ticket
and the EIC's brief filters those agents out of the schedulable roster until
the rec is approved or dismissed.

The "exclusion" event (`section_audit_excluded`) is the canonical "agent
failed at this stage on this report" signal -- one per (report, agent,
stage). The retryable per-attempt failures are logged too for forensics,
but only exclusions count towards the three-strikes rule.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import func
from sqlmodel import Session, select

from api import db as db_module
from api.models import ReportFailure, ReportStage

log = logging.getLogger("report_failures")

# Three strikes -> fire/replace ticket, EIC stops scheduling. Tracking per
# (agent, stage) so a chart-stage failure doesn't poison their analyst slot
# (and vice versa).
RECURRING_FAILURE_THRESHOLD = 3

EXCLUDED = "section_audit_excluded"


def log_failure(
    *,
    report_id: int,
    stage: ReportStage,
    failure_type: str,
    agent: str,
) -> None:
    """Append a failure row. Best-effort -- caller should not have to handle DB
    errors here, since this is observability, not the report's critical path."""
    if not agent:
        return
    try:
        with Session(db_module.engine) as session:
            session.add(ReportFailure(
                report_id=report_id,
                stage=stage,
                failure_type=failure_type,
                agent=agent,
            ))
            session.commit()
    except Exception:  # noqa: BLE001
        log.exception(
            "failed to log report_failure (report=%s agent=%s stage=%s type=%s)",
            report_id, agent, stage, failure_type,
        )


def unresolved_exclusions_by_agent_stage() -> dict[tuple[str, ReportStage], int]:
    """Map (agent_slug, stage) -> count of unresolved exclusions. Only counts
    rows of failure_type == EXCLUDED so the threshold matches the natural
    reading of 'failed at this stage three times' (i.e. three reports where
    the section had to be dropped)."""
    out: dict[tuple[str, ReportStage], int] = defaultdict(int)
    with Session(db_module.engine) as session:
        rows = session.exec(
            select(
                ReportFailure.agent,
                ReportFailure.stage,
                func.count(ReportFailure.id),  # type: ignore[arg-type]
            )
            .where(ReportFailure.failure_type == EXCLUDED)
            .where(ReportFailure.resolved_at.is_(None))  # type: ignore[union-attr]
            .group_by(ReportFailure.agent, ReportFailure.stage)
        ).all()
    for agent, stage, n in rows:
        out[(agent, stage)] = int(n)
    return dict(out)


def recurring_failures() -> list[tuple[str, ReportStage, int]]:
    """List of (agent_slug, stage, count) where count >= threshold."""
    return [
        (agent, stage, n)
        for (agent, stage), n in unresolved_exclusions_by_agent_stage().items()
        if n >= RECURRING_FAILURE_THRESHOLD
    ]


def blacklisted_slugs() -> set[str]:
    """Slugs the EIC must not schedule -- 3+ unresolved exclusions at any
    single stage. We blacklist the slug entirely rather than per-stage:
    an agent that keeps blowing up section_audit is broken from the
    scheduler's POV, full stop."""
    return {agent for (agent, _stage, _n) in recurring_failures()}


def mark_resolved_for(*, agent: str, stage: ReportStage | None = None) -> int:
    """Mark all unresolved failure rows for an agent (optionally filtered by
    stage) as resolved at now-UTC. Called when a recurring-failure fire rec
    is actioned -- approved (agent archived, counter no longer relevant) or
    dismissed (false alarm; counter resets so the EIC can use them again).
    Returns the number of rows updated."""
    now = datetime.now(UTC)
    with Session(db_module.engine) as session:
        q = select(ReportFailure).where(
            ReportFailure.agent == agent,
            ReportFailure.resolved_at.is_(None),  # type: ignore[union-attr]
        )
        if stage is not None:
            q = q.where(ReportFailure.stage == stage)
        rows = session.exec(q).all()
        for r in rows:
            r.resolved_at = now
            session.add(r)
        session.commit()
        return len(rows)
