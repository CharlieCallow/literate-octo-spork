"""Recruiter review-engine tests. Rules-based promotion + stale-fire detection."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest


@pytest.fixture
def env(monkeypatch, tmp_path):  # type: ignore[no-untyped-def]
    """Isolated SQLite + a tmp team directory containing one standing analyst."""
    from sqlmodel import SQLModel, create_engine
    eng = create_engine(f"sqlite:///{tmp_path}/recs.db", connect_args={"check_same_thread": False})
    import api.db as db_module
    import api.recruiter_review as review_module
    from api import models  # noqa: F401

    standing = tmp_path / "team"
    temp = standing / "temp"
    standing.mkdir()
    temp.mkdir()
    (standing / "macro-strategist.md").write_text(
        "# Henrik Voss — Macro Strategist\n\n**Bio.** ex-Nordea.\n", encoding="utf-8",
    )

    monkeypatch.setattr(db_module, "engine", eng)
    monkeypatch.setattr(review_module, "engine", eng)
    import api.settings as settings_module
    monkeypatch.setattr(type(settings_module.settings), "team_dir", property(lambda self: standing))
    SQLModel.metadata.create_all(eng)
    yield eng, standing, temp


def _add_report(session, slug: str, days_ago: int = 0, stage="done") -> None:  # type: ignore[no-untyped-def]
    from api.models import Report, ReportStage
    r = Report(
        theme="t",
        stage=ReportStage(stage),
        contributor_slugs=[slug],
        created_at=datetime.now(UTC) - timedelta(days=days_ago),
    )
    session.add(r)


def test_promotes_temp_with_done_report(env) -> None:  # type: ignore[no-untyped-def]
    _, _, temp = env
    (temp / "clinical-trials-analyst.md").write_text(
        "# Lina Hart — Clinical Trials Analyst\n", encoding="utf-8",
    )

    from sqlmodel import Session, select

    from api.models import Recommendation, RecommendationKind
    from api.recruiter_review import refresh_recommendations
    eng, _, _ = env
    with Session(eng) as s:
        _add_report(s, "clinical-trials-analyst")
        s.commit()

    n = refresh_recommendations()
    assert n == 1
    with Session(eng) as s:
        recs = s.exec(select(Recommendation)).all()
        assert len(recs) == 1
        assert recs[0].kind == RecommendationKind.promote
        assert recs[0].subject_slug == "clinical-trials-analyst"


def test_no_promotion_for_temp_with_no_done_reports(env) -> None:  # type: ignore[no-untyped-def]
    _, _, temp = env
    (temp / "ghost.md").write_text("# Ghost — Specialist\n", encoding="utf-8")
    from api.recruiter_review import refresh_recommendations
    assert refresh_recommendations() == 0


def test_fires_stale_standing_analyst(env) -> None:  # type: ignore[no-untyped-def]
    from sqlmodel import Session, select

    from api.models import Recommendation, RecommendationKind
    from api.recruiter_review import refresh_recommendations
    eng, _, _ = env
    with Session(eng) as s:
        _add_report(s, "macro-strategist", days_ago=45)  # old, beyond 30-day threshold
        s.commit()

    refresh_recommendations()
    with Session(eng) as s:
        recs = s.exec(
            select(Recommendation).where(Recommendation.kind == RecommendationKind.fire)
        ).all()
        assert any(r.subject_slug == "macro-strategist" for r in recs)


def test_no_fire_for_recent_assignment(env) -> None:  # type: ignore[no-untyped-def]
    from sqlmodel import Session

    from api.recruiter_review import refresh_recommendations
    eng, _, _ = env
    with Session(eng) as s:
        _add_report(s, "macro-strategist", days_ago=2)  # recent
        s.commit()
    n = refresh_recommendations()
    # Only the standing analyst exists; there's no temp + no stale, so no recs.
    assert n == 0


def test_dedupes_repeated_runs(env) -> None:  # type: ignore[no-untyped-def]
    _, _, temp = env
    (temp / "x.md").write_text("# X — Y\n", encoding="utf-8")
    from sqlmodel import Session

    from api.recruiter_review import refresh_recommendations
    eng, _, _ = env
    with Session(eng) as s:
        _add_report(s, "x")
        s.commit()
    assert refresh_recommendations() == 1
    assert refresh_recommendations() == 0  # same rec already pending
