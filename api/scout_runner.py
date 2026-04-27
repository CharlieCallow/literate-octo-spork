"""Scout digest runner: kick a daily scan, persist a ScoutRun + 10 Themes."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, time, timedelta

from sqlmodel import Session, select

from api.agents.cost import CostTracker
from api.agents.scout import Scout
from api.db import engine
from api.email import send_digest
from api.models import ScoutRun, Theme
from api.scout_parser import parse_themes
from api.settings import settings

log = logging.getLogger("scout")


def should_run_today() -> bool:
    """True if no ScoutRun has started today (local date)."""
    today = date.today()
    with Session(engine) as session:
        latest = session.exec(
            select(ScoutRun).order_by(ScoutRun.started_at.desc())  # type: ignore[attr-defined]
        ).first()
    if latest is None:
        return True
    return latest.started_at.astimezone().date() != today


def parse_hhmm(s: str) -> time | None:
    try:
        h, m = s.split(":")
        return time(int(h), int(m))
    except (ValueError, AttributeError):
        return None


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

    saved_themes: list[Theme] = []
    with Session(engine) as session:
        for p in parsed:
            t = Theme(
                scout_run_id=run_id,
                headline=p.headline,
                why_now=p.why_now,
                dig_into=p.dig_into,
                source_urls=p.source_urls,
            )
            session.add(t)
            saved_themes.append(t)
        r = session.get(ScoutRun, run_id)
        if r:
            r.n_themes = len(parsed)
            r.cost_usd = result.cost_usd
            r.finished_at = datetime.now(UTC)
            session.add(r)
        session.commit()
        for t in saved_themes:
            session.refresh(t)
        if r:
            session.refresh(r)

    # Optional email delivery; skipped silently if Resend isn't configured.
    if saved_themes:
        send_digest(saved_themes)

    return r if r else ScoutRun(id=run_id, started_at=started, n_themes=len(parsed))
