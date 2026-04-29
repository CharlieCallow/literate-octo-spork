"""Tests for the truncation-detection + glossary-placement fixes.

Real-world bug: a finished report's "Where the desk disagrees" callout
cut off mid-sentence ("...Pri") because the EIC's edit method had
max_tokens=4096. With 4-6 contributors plus opening/closing/disagreement/
bear/two-house-views the model regularly hit the cap inside the
DISAGREEMENT block (which lands AFTER the long REVISED SECTIONS).

Fix:
- editor.edit raised to max_tokens=8192 (Sonnet/Haiku ceiling).
- AgentResult.stop_reason carries the Anthropic stop_reason so callers
  can detect "max_tokens" hits.
- The edit stage flags a `max_tokens` hit on the report row so the
  dashboard surfaces a warning instead of rendering broken prose
  silently.

Plus: glossary moved out of the sections list into its own render
slot at the end (after disagreement / bear / bottom line, before
sources). Tests pin the placement.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def test_eic_edit_uses_high_max_tokens_to_avoid_truncation() -> None:
    """A 4096-token cap was the smoking gun. Pin the new cap so a
    well-meaning future edit doesn't silently regress us."""
    from api.agents.cost import CostTracker
    from api.agents.editor import EditorInChief

    cost = CostTracker(report_cap=1.0, day_cap=5.0)
    eic = EditorInChief(cost)

    captured: dict[str, object] = {}

    def fake_run(_prompt, **kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        from api.agents.base import AgentResult
        return AgentResult(text="ok", cost_usd=0.0, stop_reason="end_turn")

    with patch.object(eic, "run", side_effect=fake_run):
        eic.edit(brief="b", sections=[], chart_summary="c")

    assert captured.get("max_tokens", 0) >= 8000, (
        f"editor.edit should request >=8000 output tokens to avoid "
        f"truncation under multi-contributor reports; got "
        f"{captured.get('max_tokens')}"
    )


def test_agent_result_carries_stop_reason() -> None:
    """The agent loop must thread Anthropic's stop_reason through to
    AgentResult so callers can detect truncation. Default is None
    when unknown."""
    from api.agents.base import AgentResult

    out = AgentResult(text="x", cost_usd=0.0)
    assert out.stop_reason is None
    out2 = AgentResult(text="x", cost_usd=0.0, stop_reason="max_tokens")
    assert out2.stop_reason == "max_tokens"


def test_agent_run_records_max_tokens_audit_event() -> None:
    """When the model hits max_tokens the agent emits a max_tokens_hit
    audit event so the /workers feed shows it. Otherwise the user has
    no way to spot a truncated edit short of opening the PDF."""
    import api.agents.base as base_mod
    from api.agents.base import Agent
    from api.agents.cost import CostTracker
    from api.settings import settings

    cost = CostTracker(report_cap=1.0, day_cap=5.0)
    audit_calls: list[tuple[str, dict[str, object]]] = []

    fake_resp = MagicMock()
    fake_resp.stop_reason = "max_tokens"
    text_block = MagicMock(type="text", text="truncated...")
    fake_resp.content = [text_block]
    fake_resp.usage = MagicMock(
        input_tokens=10, output_tokens=8192,
        cache_read_input_tokens=0, cache_creation_input_tokens=0,
    )

    class FakeAnthropic:
        def __init__(self, **_kwargs) -> None:  # type: ignore[no-untyped-def]
            self.messages = MagicMock(create=MagicMock(return_value=fake_resp))

    with patch.object(base_mod, "Anthropic", FakeAnthropic):
        agent = Agent(
            persona_path=settings.team_dir / "editor-in-chief.md",
            cost=cost,
            audit=lambda evt, det: audit_calls.append((evt, det)),
        )
        result = agent.run("hello", max_tokens=8192)

    assert result.stop_reason == "max_tokens"
    events = [e for e, _ in audit_calls]
    assert "max_tokens_hit" in events


def _render_capturing_html(tmp_path: Path, **kwargs):  # type: ignore[no-untyped-def]
    """Run render_pdf with the Playwright invocation stubbed out so the
    test reads the .html file the renderer drops next to the PDF.
    Returns the assembled HTML string."""
    from api.render.pdf import render_pdf

    class FakePlaywright:
        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, *_args):  # type: ignore[no-untyped-def]
            return False

        @property
        def chromium(self):  # type: ignore[no-untyped-def]
            launcher = MagicMock()
            page = MagicMock()
            ctx = MagicMock()
            ctx.new_page.return_value = page
            browser = MagicMock()
            browser.new_context.return_value = ctx
            launcher.launch.return_value = browser
            return launcher

    with patch("api.render.pdf.sync_playwright", lambda: FakePlaywright()):
        out = tmp_path / "report.pdf"
        out.parent.mkdir(parents=True, exist_ok=True)
        render_pdf(out_path=out, **kwargs)
    return (tmp_path / "report.html").read_text(encoding="utf-8")


def test_render_pdf_glossary_lands_between_bottom_and_sources(tmp_path: Path) -> None:
    """Glossary should render in its own slot AFTER the bottom-line
    callout but BEFORE the sources list. Inspect the assembled HTML
    rather than the PDF binary so the assertion is robust."""
    from api.render.pdf import Section

    html = _render_capturing_html(
        tmp_path,
        title="Test", subtitle="sub", date="2026-04-29",
        contributors=[],
        sections=[Section(heading="Body", body_md="body text", author="A", role="R")],
        house_view_bottom="bottom",
        glossary="**TERM** — short definition.",
        sources=[{"n": "1", "url": "https://x", "title": "t", "source": "web"}],
    )

    glossary_idx = html.find(">Glossary<")
    sources_idx = html.find(">Sources<")
    bottom_idx = html.find("Bottom line.")
    assert glossary_idx > 0, "Glossary section missing from rendered HTML"
    assert sources_idx > 0, "Sources section missing"
    assert bottom_idx > 0, "Bottom line callout missing"
    assert bottom_idx < glossary_idx < sources_idx, (
        f"expected bottom < glossary < sources, got "
        f"bottom={bottom_idx}, glossary={glossary_idx}, sources={sources_idx}"
    )


# ----------------------------------------------------------------------
# Resume on done reports
# ----------------------------------------------------------------------

def test_resume_done_report_requires_explicit_from_stage() -> None:
    """A finished report shouldn't resume from the last queued job (which
    is `done`) -- that would either no-op or fall back to brief and
    silently re-run the whole pipeline. Force the user to pick a stage."""
    from fastapi import HTTPException
    from sqlmodel import Session, SQLModel, create_engine

    import api.db
    from api.models import Report, ReportStage
    from api.routes.reports import resume

    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    api.db.engine = engine

    with Session(engine) as session:
        r = Report(theme="t", stage=ReportStage.done, pdf_path="/tmp/x.pdf")
        session.add(r)
        session.commit()
        session.refresh(r)
        rid = r.id

    with Session(engine) as session, pytest.raises(HTTPException) as exc:  # type: ignore[call-overload]
        resume(rid, from_stage=None, clean_slate=False, session=session)
    assert exc.value.status_code == 400
    assert "from_stage" in exc.value.detail


def test_resume_done_report_with_from_stage_supersedes_pending_jobs() -> None:
    """When the user picks a stage to re-run on a done report, any
    leftover pending jobs from the original run must get cancelled --
    otherwise the worker could pick up an old `done` job and stamp the
    report back to done before the new stage even runs."""
    from sqlmodel import Session, SQLModel, create_engine

    import api.db
    from api.models import Job, Report, ReportStage
    from api.routes.reports import resume

    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    api.db.engine = engine

    with Session(engine) as session:
        r = Report(theme="t", stage=ReportStage.done, pdf_path="/tmp/x.pdf")
        session.add(r)
        session.commit()
        session.refresh(r)
        rid = r.id
        # An old pending job from the original run -- the runner could
        # still claim it if we don't supersede.
        session.add(Job(
            report_id=rid, stage=ReportStage.housekeeping,
            status="pending", attempts=0,
        ))
        session.commit()

    with Session(engine) as session:
        out = resume(rid, from_stage=ReportStage.edit, clean_slate=False, session=session)

    assert out.stage == ReportStage.edit
    with Session(engine) as session:
        # The new edit job is pending. The old housekeeping job got
        # superseded.
        from sqlmodel import select
        all_jobs = session.exec(
            select(Job).where(Job.report_id == rid)
        ).all()
        statuses_for_stage = {(j.stage, j.status) for j in all_jobs}
        assert (ReportStage.edit, "pending") in statuses_for_stage
        # The housekeeping job is now failed (superseded).
        assert (ReportStage.housekeeping, "failed") in statuses_for_stage


def test_resume_in_flight_report_rejected() -> None:
    """A report mid-pipeline (queued / brief / draft / etc) shouldn't be
    resumable -- you'd end up with two workers fighting over the same
    stage. Cancel or force-fail first."""
    from fastapi import HTTPException
    from sqlmodel import Session, SQLModel, create_engine

    import api.db
    from api.models import Report, ReportStage
    from api.routes.reports import resume

    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    api.db.engine = engine

    with Session(engine) as session:
        r = Report(theme="t", stage=ReportStage.draft)
        session.add(r)
        session.commit()
        session.refresh(r)
        rid = r.id

    with Session(engine) as session, pytest.raises(HTTPException) as exc:  # type: ignore[call-overload]
        resume(rid, from_stage=ReportStage.draft, clean_slate=False, session=session)
    assert exc.value.status_code == 400
    assert "in flight" in exc.value.detail


def test_render_pdf_omits_glossary_when_none(tmp_path: Path) -> None:
    """If we have no glossary text, the template must NOT render an
    empty Glossary heading -- otherwise every test-mode report ends
    with a stray heading and nothing under it."""
    html = _render_capturing_html(
        tmp_path,
        title="Test", subtitle="s", date="2026-04-29",
        contributors=[],
        sections=[],
        glossary=None,
        sources=[],
    )
    assert ">Glossary<" not in html, "empty glossary should not render a heading"
