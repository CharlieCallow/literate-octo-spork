"""data_cache round-trip via an in-memory SQLite engine."""

from __future__ import annotations

from datetime import timedelta

import pytest


@pytest.fixture
def isolated_engine(monkeypatch, tmp_path):  # type: ignore[no-untyped-def]
    """Replace the module-level engine with an in-memory SQLite + create tables."""
    from sqlmodel import SQLModel, create_engine
    eng = create_engine(f"sqlite:///{tmp_path}/test.db", connect_args={"check_same_thread": False})
    import api.data.cache as cache_module
    import api.db as db_module
    from api import models  # noqa: F401  (registers tables)

    monkeypatch.setattr(db_module, "engine", eng)
    monkeypatch.setattr(cache_module, "engine", eng)
    SQLModel.metadata.create_all(eng)
    yield eng


def test_get_cached_returns_none_on_miss(isolated_engine) -> None:  # type: ignore[no-untyped-def]
    from api.data.cache import get_cached
    assert get_cached("fred", {"series_id": "DGS10"}, ttl=timedelta(days=1)) is None


def test_round_trip(isolated_engine) -> None:  # type: ignore[no-untyped-def]
    from api.data.cache import get_cached, put_cached
    payload = {"index": ["2025-01-01"], "values": [1.0]}
    put_cached("fred", {"series_id": "DGS10"}, payload)
    got = get_cached("fred", {"series_id": "DGS10"}, ttl=timedelta(days=1))
    assert got == payload


def test_cache_miss_after_ttl(isolated_engine) -> None:  # type: ignore[no-untyped-def]
    from api.data.cache import get_cached, put_cached
    put_cached("fred", {"series_id": "X"}, {"v": 1})
    assert get_cached("fred", {"series_id": "X"}, ttl=timedelta(seconds=0)) is None


def test_distinct_queries_dont_collide(isolated_engine) -> None:  # type: ignore[no-untyped-def]
    from api.data.cache import get_cached, put_cached
    put_cached("fred", {"series_id": "A"}, {"v": "alpha"})
    put_cached("fred", {"series_id": "B"}, {"v": "beta"})
    a = get_cached("fred", {"series_id": "A"}, ttl=timedelta(days=1))
    b = get_cached("fred", {"series_id": "B"}, ttl=timedelta(days=1))
    assert a == {"v": "alpha"}
    assert b == {"v": "beta"}
