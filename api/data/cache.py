"""Postgres-backed cache for data fetches. Keyed on (source, query_hash)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlmodel import Session, select

from api.db import engine
from api.models import DataCache


def _hash(query: dict[str, Any]) -> str:
    payload = json.dumps(query, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def get_cached(source: str, query: dict[str, Any], ttl: timedelta) -> dict[str, Any] | None:
    qh = _hash(query)
    cutoff = datetime.now(UTC) - ttl
    with Session(engine) as session:
        row = session.exec(
            select(DataCache).where(
                DataCache.source == source,
                DataCache.query_hash == qh,
                DataCache.fetched_at >= cutoff,
            )
        ).first()
        return row.payload if row else None


def put_cached(source: str, query: dict[str, Any], payload: dict[str, Any]) -> None:
    qh = _hash(query)
    with Session(engine) as session:
        session.add(DataCache(source=source, query_hash=qh, payload=payload))
        session.commit()
