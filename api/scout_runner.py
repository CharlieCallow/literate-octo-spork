"""Scout digest runner: kick a daily scan, persist a ScoutRun + 10 Themes."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, select

from api.agents.cost import CostTracker
from api.agents.scout import Scout
from api.db import engine
from api.models import ScoutRun, Theme
from api.scout_parser import parse_themes
from api.settings import settings

log = logging.getLogger("scout")


def _recent_headlines(session: Session, days: int = 7) -> list[str]:
    cutoff = datetime.now(UTC) - timedelta(days=days)
    rows = session.exec(
        select(Theme).where(Theme.surfaced_at >= cutoff).order_by(Theme.surfaced_at.desc())  # type: ignore[attr-defined]
    ).all()
    return [t.headline for t in rows]


def run_scout() -> ScoutRun:
    """Run one digest. Returns the ScoutRun; themes are persisted to the DB."""
    started = datetime.now(UTC)
    with Session(engine) as session:
        run = ScoutRun(started_at=started)
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = run.id or 0
        prior = _recent_headlines(session)

    cost = CostTracker(
        report_cap=settings.cost_per_day_usd,  # use the daily cap, no per-report concept here
        day_cap=settings.cost_per_day_usd,
    )
    scout = Scout(cost=cost)

    try:
        result = scout.run_daily_digest(recent_headlines=prior)
    except Exception as e:  # noqa: BLE001
        log.exception("scout digest failed")
        with Session(engine) as session:
            r = session.get(ScoutRun, run_id)
            if r:
                r.error = f"{type(e).__name__}: {e}"
                r.finished_at = datetime.now(UTC)
                session.add(r)
                session.commit()
        raise

    parsed = parse_themes(result.text)
    log.info("scout produced %d themes (cost $%.3f)", len(parsed), result.cost_usd)

    with Session(engine) as session:
        for p in parsed:
            session.add(Theme(
                scout_run_id=run_id,
                headline=p.headline,
                why_now=p.why_now,
                dig_into=p.dig_into,
                source_urls=p.source_urls,
            ))
        r = session.get(ScoutRun, run_id)
        if r:
            r.n_themes = len(parsed)
            r.cost_usd = result.cost_usd
            r.finished_at = datetime.now(UTC)
            session.add(r)
        session.commit()
        session.refresh(r) if r else None
        return r if r else ScoutRun(id=run_id, started_at=started, n_themes=len(parsed))
