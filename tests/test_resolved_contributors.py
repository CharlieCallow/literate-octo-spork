"""Resolved-contributors tests. Honour explicit team_override; otherwise read
the brief's CONTRIBUTORS section; otherwise fall back to the full roster."""

from __future__ import annotations

from api.models import Report, ReportMode, ReportStage
from api.workflow.state_machine import ROSTER, _resolved_contributors


def _report(team: list[str] | None = None) -> Report:
    return Report(
        theme="t",
        mode=ReportMode.standard,
        stage=ReportStage.queued,
        team_override=team or [],
    )


def test_team_override_wins_over_brief() -> None:
    brief = "# CONTRIBUTORS\n- `macro-strategist`: x\n"
    out = _resolved_contributors(_report(["equity-analyst"]), brief)
    assert [c["slug"] for c in out] == ["equity-analyst"]


def test_unknown_override_slugs_filtered_then_fallback() -> None:
    out = _resolved_contributors(_report(["nobody-here"]), "# CONTRIBUTORS\n- `macro-strategist`: x\n")
    # Unknown override drops back to brief.
    assert [c["slug"] for c in out] == ["macro-strategist"]


def test_brief_used_when_no_override() -> None:
    brief = "# CONTRIBUTORS\n- `equity-analyst`: x\n"
    out = _resolved_contributors(_report(), brief)
    assert [c["slug"] for c in out] == ["equity-analyst"]


def test_falls_back_to_full_roster_if_brief_empty() -> None:
    out = _resolved_contributors(_report(), "")
    assert [c["slug"] for c in out] == [c["slug"] for c in ROSTER]
