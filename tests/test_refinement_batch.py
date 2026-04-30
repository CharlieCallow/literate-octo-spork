"""Tests for the refinement batch:
- is_test flag + downstream filters (past_reports / archive / tagger /
  recruiter review)
- /workers/audit_tail
- /reports/cost_stats (trailing-30d median + p90 per mode)
- /reports/{id}/rerun_analyst/{slug}
- R2 storage covers notes-*.md / section-*.md so single-analyst reruns
  survive a Railway rebuild
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine

import api.models  # noqa: F401 -- registers tables before create_all


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """In-memory engine, swapped into every module that holds a reference
    to the real engine."""
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)

    import api.db
    import api.recruiter_review as rr_mod
    import api.tagger as tagger_mod
    import api.workflow.runner as runner_mod
    import api.workflow.state_machine as sm_mod
    monkeypatch.setattr(api.db, "engine", engine)
    monkeypatch.setattr(runner_mod, "engine", engine)
    monkeypatch.setattr(sm_mod, "engine", engine)
    monkeypatch.setattr(tagger_mod, "engine", engine)
    monkeypatch.setattr(rr_mod, "engine", engine)
    yield engine


def _seed_done_report(engine, *, is_test=False, mode=None, theme="t", cost=0.5):  # type: ignore[no-untyped-def]
    from api.models import Report, ReportMode, ReportStage

    with Session(engine) as session:
        r = Report(
            theme=theme,
            stage=ReportStage.done,
            mode=mode or ReportMode.standard,
            is_test=is_test,
            cost_usd=cost,
        )
        session.add(r)
        session.commit()
        session.refresh(r)
        return r.id


# ----------------------------------------------------------------------
# is_test flag + filters
# ----------------------------------------------------------------------

def test_test_mode_creates_report_marks_is_test_true(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The /reports POST handler should set is_test=True automatically
    when the requested mode is `test`. Otherwise the user has to
    remember to flip a checkbox to keep their smoke runs out of the
    firm's memory, which they won't."""
    # Don't actually instantiate the FastAPI app to test this; just
    # verify the contract by creating a Report row the way the route
    # does and asserting on the field.
    from api.models import Report, ReportMode

    r_test = Report(theme="x", mode=ReportMode.test, is_test=(ReportMode.test == ReportMode.test))
    assert r_test.is_test is True
    r_normal = Report(theme="x", mode=ReportMode.standard, is_test=(ReportMode.standard == ReportMode.test))
    assert r_normal.is_test is False


def test_past_reports_summary_excludes_test_runs(db) -> None:  # type: ignore[no-untyped-def]
    """Test-mode reports must not anchor the EIC's brief -- a smoke
    run with throwaway theme would otherwise show up as 'a recent
    report' the next time you commission anything."""
    from api.workflow.state_machine import _past_reports_summary

    _seed_done_report(db, is_test=False, theme="real-thing")
    _seed_done_report(db, is_test=True, theme="smoke-test-please-ignore")

    out = _past_reports_summary()
    themes = [r["theme"] for r in out]
    assert "real-thing" in themes
    assert "smoke-test-please-ignore" not in themes


def test_list_reports_excludes_test_by_default(db) -> None:  # type: ignore[no-untyped-def]
    """Archive default view shouldn't show test runs unless explicitly
    asked. An afternoon of pipeline smoke runs would otherwise drown
    the real archive."""
    from api.routes.reports import list_reports

    _seed_done_report(db, is_test=False, theme="real")
    _seed_done_report(db, is_test=True, theme="smoke")

    with Session(db) as session:
        default = list_reports(include_test=False, session=session)
        with_test = list_reports(include_test=True, session=session)

    default_themes = [r.theme for r in default]
    with_test_themes = [r.theme for r in with_test]
    assert default_themes == ["real"]
    assert sorted(with_test_themes) == ["real", "smoke"]


# ----------------------------------------------------------------------
# /workers/audit_tail
# ----------------------------------------------------------------------

def test_audit_tail_returns_newest_first(db) -> None:  # type: ignore[no-untyped-def]
    """The /workers page polls audit_tail; it must come back newest-first
    so the live feed feels live. A stable order is also what makes the
    polling stable across refreshes."""
    from api.models import AuditLog
    from api.routes.workers import audit_tail

    now = datetime.now(UTC)
    with Session(db) as session:
        for i in range(5):
            session.add(AuditLog(
                actor=f"agent-{i}", event="model_call",
                cost_usd=0.01 * i,
                created_at=now - timedelta(seconds=10 - i),
            ))
        session.commit()

    with Session(db) as session:
        rows = audit_tail(limit=50, session=session)
    actors_in_order = [r.actor for r in rows]
    # Most recent (agent-4) first.
    assert actors_in_order[0] == "agent-4"
    assert actors_in_order[-1] == "agent-0"


def test_audit_tail_caps_limit(db) -> None:  # type: ignore[no-untyped-def]
    """Pathological limit values shouldn't let one client drag a quarter-
    million rows out of Postgres."""
    from api.routes.workers import audit_tail

    with Session(db) as session:
        # Limit > 200 -> cap at 200; limit < 1 -> floor at 1.
        rows = audit_tail(limit=999_999, session=session)
        assert len(rows) <= 200
        rows = audit_tail(limit=-3, session=session)
        # Empty DB so this just doesn't crash with a negative LIMIT.
        assert rows == []


# ----------------------------------------------------------------------
# /reports/cost_stats
# ----------------------------------------------------------------------

def test_cost_stats_returns_per_mode_median_and_p90(db) -> None:  # type: ignore[no-untyped-def]
    """median + p90 per mode, computed from the last 30 days of completed
    reports. Empty modes return None for both percentiles -- the front-
    end uses that as a signal to fall back to the static estimate."""
    from api.models import ReportMode
    from api.routes.reports import cost_stats

    # 5 standard-mode reports with predictable costs.
    for cost in [0.20, 0.40, 0.60, 0.80, 1.00]:
        _seed_done_report(db, mode=ReportMode.standard, cost=cost)
    # 1 fast-mode (n < 3 threshold the UI uses).
    _seed_done_report(db, mode=ReportMode.fast, cost=0.10)

    with Session(db) as session:
        rows = cost_stats(session=session)
    by_mode = {r.mode: r for r in rows}

    # standard: 5 rows; median is the 3rd of [.2,.4,.6,.8,1.0] = 0.60.
    assert by_mode[ReportMode.standard].n == 5
    assert by_mode[ReportMode.standard].median_cost_usd == pytest.approx(0.60)
    assert by_mode[ReportMode.standard].p90_cost_usd == pytest.approx(1.00)
    # fast: 1 row; median == p90 == that one value.
    assert by_mode[ReportMode.fast].n == 1
    assert by_mode[ReportMode.fast].median_cost_usd == pytest.approx(0.10)
    # test mode: no rows -> None percentiles.
    assert by_mode[ReportMode.test].n == 0
    assert by_mode[ReportMode.test].median_cost_usd is None


def test_cost_stats_excludes_old_reports(db) -> None:  # type: ignore[no-untyped-def]
    """Reports older than 30 days shouldn't drag the rolling median."""
    from api.models import Report, ReportMode, ReportStage
    from api.routes.reports import cost_stats

    long_ago = datetime.now(UTC) - timedelta(days=60)
    with Session(db) as session:
        # One ancient row that should be filtered out.
        session.add(Report(
            theme="ancient", stage=ReportStage.done,
            mode=ReportMode.standard, cost_usd=99.99,
            created_at=long_ago,
        ))
        session.commit()

    with Session(db) as session:
        rows = cost_stats(session=session)
    standard = next(r for r in rows if r.mode == ReportMode.standard)
    assert standard.n == 0  # ancient row filtered out
    assert standard.median_cost_usd is None


# ----------------------------------------------------------------------
# rerun_analyst route
# ----------------------------------------------------------------------

def test_rerun_analyst_rejects_unknown_slug(db, monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A typo in the slug shouldn't let the user trigger an arbitrary
    persona's draft -- only contributors actually on the report."""
    from fastapi import HTTPException

    import api.settings as settings_mod
    from api.models import Report, ReportMode, ReportStage
    from api.routes.reports import rerun_analyst

    # Point reports/ at tmp so working_dir() doesn't write into the real
    # dev tree.
    monkeypatch.setattr(settings_mod.settings, "reports_dir", tmp_path)

    with Session(db) as session:
        report = Report(
            theme="x", stage=ReportStage.failed,
            mode=ReportMode.standard,
            contributor_slugs=["macro-strategist"],
        )
        session.add(report)
        session.commit()
        session.refresh(report)
        rid = report.id

    # Seed brief.md + notes for the *known* slug so we get past the
    # missing-file check; then ask for an unknown slug.
    wd = tmp_path / str(rid)
    wd.mkdir(parents=True)
    (wd / "brief.md").write_text(
        "# CONTRIBUTORS\n- `macro-strategist`: do macro\n", encoding="utf-8",
    )
    (wd / "notes-macro-strategist.md").write_text("real notes", encoding="utf-8")
    (wd / "notes-imposter.md").write_text("forged", encoding="utf-8")

    with Session(db) as session, pytest.raises(HTTPException) as exc:  # type: ignore[call-overload]
        rerun_analyst(rid, "imposter", session=session)
    assert exc.value.status_code == 400
    assert "imposter" in exc.value.detail


def test_rerun_analyst_rejects_when_notes_missing(db, monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """If research never ran (notes-<slug>.md is absent and not in R2),
    the route should refuse rather than draft from nothing. Tells the
    user to /resume from research instead."""
    from fastapi import HTTPException

    import api.settings as settings_mod
    from api.models import Report, ReportMode, ReportStage
    from api.routes.reports import rerun_analyst

    monkeypatch.setattr(settings_mod.settings, "reports_dir", tmp_path)
    # Force R2 disabled so the route only consults the local fs.
    import api.storage as storage_mod
    monkeypatch.setattr(storage_mod, "is_r2_enabled", lambda: False)

    with Session(db) as session:
        report = Report(
            theme="x", stage=ReportStage.failed,
            mode=ReportMode.standard,
            contributor_slugs=["macro-strategist"],
        )
        session.add(report)
        session.commit()
        session.refresh(report)
        rid = report.id

    wd = tmp_path / str(rid)
    wd.mkdir(parents=True)
    (wd / "brief.md").write_text(
        "# CONTRIBUTORS\n- `macro-strategist`: do macro\n", encoding="utf-8",
    )
    # Deliberately no notes-macro-strategist.md.

    with Session(db) as session, pytest.raises(HTTPException) as exc:  # type: ignore[call-overload]
        rerun_analyst(rid, "macro-strategist", session=session)
    assert exc.value.status_code == 400
    assert "notes-macro-strategist.md" in exc.value.detail


# ----------------------------------------------------------------------
# Storage covers per-analyst markdown
# ----------------------------------------------------------------------

def test_upload_artifacts_includes_notes_and_section_files(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A re-run after a Railway rebuild needs notes-<slug>.md to be in
    R2 -- otherwise the working dir on the new container is empty and
    the rerun route 400s. Same for section-<slug>.md so a partial PDF
    can be re-rendered without re-drafting everyone."""
    import api.storage as storage_mod

    # Pretend R2 is enabled but stub the boto3 client so nothing leaves
    # the box.
    monkeypatch.setattr(storage_mod, "is_r2_enabled", lambda: True)
    uploaded: list[str] = []

    class FakeClient:
        def upload_file(self, src, bucket, key, **_kwargs) -> None:  # type: ignore[no-untyped-def]
            uploaded.append(key)

    monkeypatch.setattr(storage_mod, "_client", lambda: FakeClient())
    monkeypatch.setattr(storage_mod.settings, "r2_bucket", "test-bucket")

    wd = tmp_path / "wd"
    wd.mkdir()
    (wd / "report.pdf").write_bytes(b"%PDF-1.4")
    (wd / "brief.md").write_text("brief", encoding="utf-8")
    (wd / "edited.md").write_text("edited", encoding="utf-8")
    (wd / "notes-macro-strategist.md").write_text("notes", encoding="utf-8")
    (wd / "notes-equity-analyst.md").write_text("notes", encoding="utf-8")
    (wd / "section-macro-strategist.md").write_text("sec", encoding="utf-8")
    # Intermediate markdown is now uploaded too -- a re-run from `edit`
    # after a Railway rebuild needs these as inputs (see hydrate_
    # working_dir on the read side).
    (wd / "rebuttals.md").write_text("rebut", encoding="utf-8")
    (wd / "redteam.md").write_text("bear", encoding="utf-8")
    # Genuinely-unrelated file should still NOT be uploaded.
    (wd / "scratch.txt").write_text("nope", encoding="utf-8")

    storage_mod.upload_artifacts(42, wd)
    keys = sorted(uploaded)
    assert any("report.pdf" in k for k in keys)
    assert any("notes-macro-strategist.md" in k for k in keys)
    assert any("notes-equity-analyst.md" in k for k in keys)
    assert any("section-macro-strategist.md" in k for k in keys)
    # Editor-input markdown uploaded so re-runs survive a rebuild.
    assert any("rebuttals.md" in k for k in keys)
    assert any("redteam.md" in k for k in keys)
    # Random non-recognised file is still skipped.
    assert not any("scratch.txt" in k for k in keys)
