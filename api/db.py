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
    with engine.connect() as conn:
        # Add reports.mode if it predates the M2 cost-mode work.
        try:
            conn.exec_driver_sql(
                "ALTER TABLE reports ADD COLUMN mode VARCHAR DEFAULT 'standard'"
            )
            conn.commit()
        except Exception:
            pass  # column already exists


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
