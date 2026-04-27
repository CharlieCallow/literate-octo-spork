"""Tests for the scout schedule helpers."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

import pytest

from api.scout_runner import parse_hhmm, should_run_today


def test_parse_hhmm_valid() -> None:
    assert parse_hhmm("07:00") == time(7, 0)
    assert parse_hhmm("23:59") == time(23, 59)


def test_parse_hhmm_invalid_returns_none() -> None:
    assert parse_hhmm("garbage") is None
    assert parse_hhmm("") is None
    assert parse_hhmm("25:00") is None  # ValueError on int(25) -> time()


@pytest.fixture
def isolated_engine(monkeypatch, tmp_path):  # type: ignore[no-untyped-def]
    from sqlmodel import SQLModel, create_engine
    eng = create_engine(f"sqlite:///{tmp_path}/sched.db", connect_args={"check_same_thread": False})
    import api.db as db_module
    import api.scout_runner as runner_module
    from api import models  # noqa: F401

    monkeypatch.setattr(db_module, "engine", eng)
    monkeypatch.setattr(runner_module, "engine", eng)
    SQLModel.metadata.create_all(eng)
    yield eng


def test_should_run_when_no_runs_yet(isolated_engine) -> None:  # type: ignore[no-untyped-def]
    assert should_run_today() is True


def test_should_not_run_when_already_ran_today(isolated_engine) -> None:  # type: ignore[no-untyped-def]
    from sqlmodel import Session

    from api.models import ScoutRun
    with Session(isolated_engine) as s:
        s.add(ScoutRun(started_at=datetime.now(UTC), n_themes=10))
        s.commit()
    assert should_run_today() is False


def test_should_run_when_last_run_was_yesterday(isolated_engine) -> None:  # type: ignore[no-untyped-def]
    from sqlmodel import Session

    from api.models import ScoutRun
    yesterday = datetime.now(UTC) - timedelta(days=1, hours=2)
    with Session(isolated_engine) as s:
        s.add(ScoutRun(started_at=yesterday, n_themes=10))
        s.commit()
    assert should_run_today() is True
