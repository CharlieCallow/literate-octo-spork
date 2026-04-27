"""SQLModel engine + session helpers."""

from __future__ import annotations

from collections.abc import Iterator

from sqlmodel import Session, SQLModel, create_engine

from api.settings import settings

_connect_args: dict[str, object] = {}
if settings.database_url.startswith("sqlite"):
    _connect_args["check_same_thread"] = False

engine = create_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
    connect_args=_connect_args,
)


def init_db() -> None:
    # Importing models here ensures tables register with SQLModel metadata.
    from api import models  # noqa: F401
    SQLModel.metadata.create_all(engine)
    _migrate_sqlite()


def _migrate_sqlite() -> None:
    """Add columns introduced after the first ship. SQLite-only; no-op elsewhere."""
    if not settings.database_url.startswith("sqlite"):
        return
    additions: list[tuple[str, str]] = [
        # M2 cost-mode work
        ("reports", "ALTER TABLE reports ADD COLUMN mode VARCHAR DEFAULT 'standard'"),
        # M2 sprint 3: per-stage cost + retry scheduling
        ("jobs",    "ALTER TABLE jobs ADD COLUMN cost_usd REAL DEFAULT 0.0"),
        ("jobs",    "ALTER TABLE jobs ADD COLUMN run_after TIMESTAMP"),
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
