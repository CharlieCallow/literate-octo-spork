"""SQLModel engine + session helpers."""

from __future__ import annotations

from collections.abc import Iterator

from sqlmodel import Session, SQLModel, create_engine

from api.settings import settings


def _normalise_db_url(url: str) -> str:
    """Force the psycopg v3 driver for Postgres URLs.

    Managed providers (Railway, Heroku, Render) emit `postgres://...` or
    `postgresql://...`. SQLAlchemy maps both to the legacy `psycopg2` dialect
    by default; we install psycopg (v3) instead, so we rewrite the scheme to
    pin the right dialect and skip the missing-module crash."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://") and "+psycopg" not in url:
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


_DB_URL = _normalise_db_url(settings.database_url)

_connect_args: dict[str, object] = {}
if _DB_URL.startswith("sqlite"):
    _connect_args["check_same_thread"] = False

engine = create_engine(
    _DB_URL,
    echo=False,
    pool_pre_ping=True,
    connect_args=_connect_args,
)


def init_db() -> None:
    # Importing models here ensures tables register with SQLModel metadata.
    from api import models  # noqa: F401
    SQLModel.metadata.create_all(engine)
    _migrate_sqlite()

    # Persona persistence: seed from FS the first time, then hydrate FS from
    # DB so promotions / firings / manual edits survive Railway rebuilds.
    from api.personas import hydrate_filesystem, seed_from_filesystem
    seed_from_filesystem()
    hydrate_filesystem()

    # Reports' PDFs still live on the ephemeral filesystem (R2 storage is the
    # proper fix). Clear pdf_path on rows whose file is gone so the dashboard
    # doesn't show broken iframe links.
    _clear_stale_pdf_paths()
    _scrub_report_subtitles()


def _scrub_report_subtitles() -> None:
    """Strip leftover <antcite> / <cite> / mangled variants from subtitles
    written by older builds before the agent-output sanitizer existed."""
    from sqlmodel import select

    from api.agents.base import sanitize_agent_text
    from api.models import Report
    with Session(engine) as session:
        rows = session.exec(
            select(Report).where(Report.subtitle.is_not(None))  # type: ignore[union-attr]
        ).all()
        changed = 0
        for r in rows:
            if not r.subtitle:
                continue
            cleaned = sanitize_agent_text(r.subtitle).strip()
            if cleaned != r.subtitle:
                r.subtitle = cleaned
                session.add(r)
                changed += 1
        if changed:
            session.commit()


def _clear_stale_pdf_paths() -> None:
    """Set pdf_path = NULL on any report whose PDF file is no longer on disk."""
    from pathlib import Path

    from sqlmodel import select

    from api.models import Report
    with Session(engine) as session:
        rows = session.exec(
            select(Report).where(Report.pdf_path.is_not(None))  # type: ignore[union-attr]
        ).all()
        cleared = 0
        for r in rows:
            if r.pdf_path and not Path(r.pdf_path).exists():
                r.pdf_path = None
                session.add(r)
                cleared += 1
        if cleared:
            session.commit()


def _migrate_sqlite() -> None:
    """Add columns introduced after the first ship. SQLite-only; no-op elsewhere."""
    if _DB_URL.startswith("sqlite"):
        additions: list[tuple[str, str]] = [
            # M2 cost-mode work
            ("reports", "ALTER TABLE reports ADD COLUMN mode VARCHAR DEFAULT 'standard'"),
            # M2 sprint 3: per-stage cost + retry scheduling
            ("jobs",    "ALTER TABLE jobs ADD COLUMN cost_usd REAL DEFAULT 0.0"),
            ("jobs",    "ALTER TABLE jobs ADD COLUMN run_after TIMESTAMP"),
            # M2 sprint 4: per-report budget + team override
            ("reports", "ALTER TABLE reports ADD COLUMN budget_cap_usd REAL"),
            ("reports", "ALTER TABLE reports ADD COLUMN team_override JSON DEFAULT '[]'"),
            # M3: Scout themes are linked back to the report they spawn
            ("themes",  "ALTER TABLE themes ADD COLUMN commissioned_report_id INTEGER"),
            # M4: contributor_slugs records who actually worked on the report
            ("reports", "ALTER TABLE reports ADD COLUMN contributor_slugs JSON DEFAULT '[]'"),
            # Public share link
            ("reports", "ALTER TABLE reports ADD COLUMN share_token VARCHAR"),
            ("reports", "ALTER TABLE reports ADD COLUMN shared_at TIMESTAMP"),
        ]
        with engine.connect() as conn:
            for _table, sql in additions:
                try:
                    conn.exec_driver_sql(sql)
                    conn.commit()
                except Exception:
                    pass  # column already exists
        return

    if _DB_URL.startswith("postgresql"):
        # Postgres prod columns added before this commit were applied by hand /
        # by table recreate. Use IF NOT EXISTS so this stays idempotent.
        pg_additions: list[str] = [
            "ALTER TABLE reports ADD COLUMN IF NOT EXISTS share_token VARCHAR",
            "ALTER TABLE reports ADD COLUMN IF NOT EXISTS shared_at TIMESTAMP",
            "CREATE INDEX IF NOT EXISTS ix_reports_share_token ON reports (share_token)",
        ]
        with engine.connect() as conn:
            for sql in pg_additions:
                try:
                    conn.exec_driver_sql(sql)
                    conn.commit()
                except Exception:
                    pass


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
