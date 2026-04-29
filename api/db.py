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
            # Close-the-loop: theme graph fields on the report row.
            ("reports", "ALTER TABLE reports ADD COLUMN mentioned_tickers JSON DEFAULT '[]'"),
            ("reports", "ALTER TABLE reports ADD COLUMN mentioned_themes JSON DEFAULT '[]'"),
            # Extend-the-surface: source-diversity flag.
            ("reports", "ALTER TABLE reports ADD COLUMN max_domain_share REAL"),
            ("reports", "ALTER TABLE reports ADD COLUMN top_domain VARCHAR"),
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
            # Close-the-loop: theme graph fields on the report row.
            "ALTER TABLE reports ADD COLUMN IF NOT EXISTS mentioned_tickers JSON DEFAULT '[]'::json",
            "ALTER TABLE reports ADD COLUMN IF NOT EXISTS mentioned_themes JSON DEFAULT '[]'::json",
            "ALTER TABLE reports ADD COLUMN IF NOT EXISTS max_domain_share DOUBLE PRECISION",
            "ALTER TABLE reports ADD COLUMN IF NOT EXISTS top_domain VARCHAR",
            # uploaded_documents: created_all() handles fresh deploys; this
            # CREATE-IF-NOT-EXISTS keeps the table present on existing prod
            # databases that pre-date the upload feature.
            """
            CREATE TABLE IF NOT EXISTS uploaded_documents (
              id SERIAL PRIMARY KEY,
              report_id INTEGER NOT NULL REFERENCES reports(id),
              filename VARCHAR NOT NULL,
              mime VARCHAR NOT NULL,
              size_bytes INTEGER NOT NULL DEFAULT 0,
              summary TEXT NOT NULL DEFAULT '',
              extracted_text TEXT NOT NULL DEFAULT '',
              created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """,
            "CREATE INDEX IF NOT EXISTS ix_uploaded_documents_report_id ON uploaded_documents (report_id)",
        ]
        with engine.connect() as conn:
            for sql in pg_additions:
                try:
                    conn.exec_driver_sql(sql)
                    conn.commit()
                except Exception:
                    pass

        # Postgres enum migration. SQLModel.metadata.create_all does NOT
        # ALTER existing enum types when the Python enum gains new values.
        # Sync any missing values now so an enum addition (e.g. the
        # `rebuttal` stage) doesn't blow up the next INSERT with
        # InvalidTextRepresentation.
        _sync_pg_enums()


# Map of Postgres enum-type-name -> Python enum class. Add an entry when
# you introduce a new enum-typed column. The migration runs at startup
# and is idempotent (each ADD VALUE is wrapped in its own savepoint).
def _enum_sync_map() -> list[tuple[str, type]]:
    from api.models import (
        CallDirection,
        CallOutcome,
        PersonaStatus,
        RecommendationKind,
        RecommendationStatus,
        ReportMode,
        ReportStage,
    )
    return [
        ("reportstage",          ReportStage),
        ("reportmode",           ReportMode),
        ("personastatus",        PersonaStatus),
        ("recommendationkind",   RecommendationKind),
        ("recommendationstatus", RecommendationStatus),
        ("calldirection",        CallDirection),
        ("calloutcome",          CallOutcome),
    ]


def _sync_pg_enums() -> None:
    """For each registered (type_name, EnumClass), ALTER TYPE ADD VALUE
    for any Python enum value that's missing from the Postgres type.

    Each ADD VALUE runs in its own savepoint so a single failure (e.g.
    a value that's already been added by another instance racing the
    migration) doesn't poison the rest of the sync."""
    import logging
    log = logging.getLogger("db.migrate")
    for type_name, enum_cls in _enum_sync_map():
        try:
            with engine.connect() as conn:
                # Read what's already in the Postgres type.
                rows = conn.exec_driver_sql(
                    "SELECT enumlabel FROM pg_enum e "
                    "JOIN pg_type t ON e.enumtypid = t.oid "
                    "WHERE t.typname = %s",
                    (type_name,),
                ).fetchall()
                existing = {r[0] for r in rows}
                if not existing:
                    # Type doesn't exist yet -- create_all() will create
                    # it with all current values, so nothing to migrate.
                    continue
                for member in enum_cls:
                    if member.value in existing:
                        continue
                    log.info(
                        "syncing enum %s: ADD VALUE %r", type_name, member.value,
                    )
                    try:
                        # ALTER TYPE ... ADD VALUE can't run inside a
                        # transaction block in older Postgres; use
                        # AUTOCOMMIT for the duration of this statement.
                        ac = conn.execution_options(isolation_level="AUTOCOMMIT")
                        ac.exec_driver_sql(
                            f"ALTER TYPE {type_name} ADD VALUE IF NOT EXISTS '{member.value}'"
                        )
                    except Exception:  # noqa: BLE001
                        log.exception(
                            "could not add %r to enum %s -- continuing",
                            member.value, type_name,
                        )
        except Exception:  # noqa: BLE001
            log.exception("enum sync for %s failed (non-blocking)", type_name)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
