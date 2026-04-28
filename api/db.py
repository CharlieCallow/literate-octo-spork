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


def _migrate_sqlite() -> None:
    """Add columns introduced after the first ship. SQLite-only; no-op elsewhere."""
    if not _DB_URL.startswith("sqlite"):
        return
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
    ]
    with engine.connect() as conn:
        for _table, sql in additions:
            try:
                conn.exec_driver_sql(sql)
                conn.commit()
            except Exception:
                pass  # column already exists


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
