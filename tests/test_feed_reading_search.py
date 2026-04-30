"""Tests for the RSS feed + reading-time/claim-density metrics +
archive search enhancements.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine

import api.models  # noqa: F401 -- registers tables before create_all


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    import api.db
    monkeypatch.setattr(api.db, "engine", engine)
    yield engine


def _seed_done(engine, *, theme="t", subtitle="", is_test=False, share_token=None):  # type: ignore[no-untyped-def]
    from api.models import Report, ReportMode, ReportStage

    with Session(engine) as session:
        r = Report(
            theme=theme, subtitle=subtitle, stage=ReportStage.done,
            mode=ReportMode.standard, is_test=is_test,
            share_token=share_token,
        )
        session.add(r)
        session.commit()
        session.refresh(r)
        return r.id


# ----------------------------------------------------------------------
# RSS feed
# ----------------------------------------------------------------------

def test_feed_lists_recent_done_reports(db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The RSS feed should include recently-completed real reports
    (not test runs, not in-flight). Each `<item>` carries a title +
    link + pubDate so a feed reader can render it cleanly."""
    from api.routes.reports import feed
    from api.settings import settings as settings_obj
    monkeypatch.setattr(settings_obj, "public_base_url", "https://forte.example.com")

    _seed_done(db, theme="Real one", subtitle="ships")
    _seed_done(db, theme="Smoke run", is_test=True)

    with Session(db) as session:
        resp = feed(session=session)

    body = resp.body.decode() if hasattr(resp, "body") else resp.body  # type: ignore[union-attr]
    assert "Real one" in body
    assert "Smoke run" not in body, "test-mode runs must not appear in the feed"
    assert "<rss" in body
    assert "<channel>" in body
    assert "Forte Research" in body


def test_feed_uses_share_link_when_token_minted(db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """When a report has a public share token, the RSS link should
    point at the token URL (which is unauthenticated). Otherwise readers
    bounce off the basic-auth gate on the dashboard URL."""
    from api.routes.reports import feed
    from api.settings import settings as settings_obj
    monkeypatch.setattr(settings_obj, "public_base_url", "https://forte.example.com")

    rid = _seed_done(db, theme="Shared one", share_token="ttkk")

    with Session(db) as session:
        resp = feed(session=session)
    body = resp.body.decode()  # type: ignore[union-attr]
    assert f"share/{rid}/ttkk" in body


def test_feed_handles_missing_public_base_url(db) -> None:  # type: ignore[no-untyped-def]
    """No PUBLIC_BASE_URL env var -> still produce a valid feed with
    relative links. Feed readers usually resolve against the feed's
    own URL anyway."""
    from api.routes.reports import feed

    _seed_done(db, theme="No-base report")
    with Session(db) as session:
        resp = feed(session=session)
    body = resp.body.decode()  # type: ignore[union-attr]
    assert "No-base report" in body
    assert "<rss" in body  # well-formed even with empty base


def test_feed_excludes_old_reports(db) -> None:  # type: ignore[no-untyped-def]
    """The feed window is 180 days. Older reports drop off so the feed
    doesn't grow unbounded."""
    from api.models import Report, ReportMode, ReportStage
    from api.routes.reports import feed

    long_ago = datetime.now(UTC) - timedelta(days=400)
    with Session(db) as session:
        session.add(Report(
            theme="ancient", stage=ReportStage.done,
            mode=ReportMode.standard, is_test=False,
            created_at=long_ago,
        ))
        session.commit()

    with Session(db) as session:
        resp = feed(session=session)
    body = resp.body.decode()  # type: ignore[union-attr]
    assert "ancient" not in body


# ----------------------------------------------------------------------
# Reading-time + claim-density
# ----------------------------------------------------------------------

def test_report_out_carries_reading_metrics(db) -> None:  # type: ignore[no-untyped-def]
    """Once render fills in word_count / read_minutes / claim_density,
    ReportOut must surface them so the dashboard can render the
    'X min read · Y words · Z claims per 100 words' line."""
    from api.models import Report, ReportMode, ReportStage
    from api.routes.reports import ReportOut

    with Session(db) as session:
        r = Report(
            theme="t", stage=ReportStage.done, mode=ReportMode.standard,
            word_count=2200, read_minutes=10, claim_density=2.7,
        )
        session.add(r)
        session.commit()
        session.refresh(r)

    out = ReportOut.from_db(r)
    assert out.word_count == 2200
    assert out.read_minutes == 10
    assert out.claim_density == 2.7


def test_report_out_handles_unmeasured_legacy_rows() -> None:
    """Reports rendered before this commit have NULL metrics. ReportOut
    must pass them through as None rather than coerce to 0 -- otherwise
    the UI would show '0 min read' for every old row."""
    from api.models import Report, ReportMode, ReportStage
    from api.routes.reports import ReportOut

    r = Report(theme="t", stage=ReportStage.done, mode=ReportMode.standard)
    out = ReportOut.from_db(r)
    assert out.word_count is None
    assert out.read_minutes is None
    assert out.claim_density is None


# ----------------------------------------------------------------------
# Archive search via theme tags
# ----------------------------------------------------------------------

def test_report_out_surfaces_mentioned_tags(db) -> None:  # type: ignore[no-untyped-def]
    """The archive page's search box wants to match against ticker /
    theme tags; ReportOut must include the lists for that to work."""
    from api.models import Report, ReportMode, ReportStage
    from api.routes.reports import ReportOut

    with Session(db) as session:
        r = Report(
            theme="t", stage=ReportStage.done, mode=ReportMode.standard,
            mentioned_tickers=["NVDA", "AMD"],
            mentioned_themes=["semis", "ai-capex"],
        )
        session.add(r)
        session.commit()
        session.refresh(r)

    out = ReportOut.from_db(r)
    assert out.mentioned_tickers == ["NVDA", "AMD"]
    assert out.mentioned_themes == ["semis", "ai-capex"]
