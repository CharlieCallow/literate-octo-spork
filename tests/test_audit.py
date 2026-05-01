"""section_audit gate: per-section failure-marker detection.

The gate runs between draft and rebuttal. Its only job at this layer is
pattern-matching: given a section body, does it contain a failure marker
that disqualifies the section? Retry/exclusion logic lives in the stage
itself; we test the predicate here against synthetic fixtures (one file
per marker from the ticket) plus a clean control.

Reference: tests/fixtures/section_audit/historical_failures.md documents the
0/4 expectation against the four shipped reports the gate was designed for.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from api.workflow.state_machine import (
    SECTION_FAILURE_MARKERS,
    _section_failure_marker,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "section_audit"


# Each marker from the ticket has a synthetic fixture file. The mapping ties
# a fixture filename to the substring we expect the predicate to surface.
FAILURE_FIXTURES: dict[str, str] = {
    "agent_halted.md": "agent halted",
    "data_pull_failed.md": "data pull failed",
    "notes_failed_to_load.md": "notes failed to load",
    "section_pulled.md": "section pulled",
    "equity_view_will_arrive.md": "the equity view will arrive",
    "we_did_not_answer.md": "we did not answer",
    "shipping_that_gap.md": "shipping that gap",
    "retry_next_cycle.md": "retry next cycle",
    "we_are_not_going_to_manufacture.md": "we are not going to manufacture",
}


@pytest.mark.parametrize(("fixture", "expected_marker"), list(FAILURE_FIXTURES.items()))
def test_failure_marker_flagged(fixture: str, expected_marker: str) -> None:
    """Each ticketed marker, dropped into a synthetic section, is flagged."""
    body = (FIXTURE_DIR / fixture).read_text()
    found = _section_failure_marker(body)
    assert found == expected_marker, (
        f"{fixture}: expected marker {expected_marker!r}, got {found!r}"
    )


def test_clean_section_passes() -> None:
    """Control: a clean section with no markers must not trip the gate."""
    body = (FIXTURE_DIR / "clean_section.md").read_text()
    assert _section_failure_marker(body) is None


def test_marker_in_quote_still_flags() -> None:
    """False-positive trap: a marker phrase inside a quote-style retrospective
    still trips the gate. Per the ticket's call, we accept the false-positive
    cost (one re-run) over the false-negative cost (shipping the failure mode
    we've been shipping). Test pins that decision so a future change to
    whitelist quote/code blocks is a deliberate one."""
    body = (FIXTURE_DIR / "marker_in_quote.md").read_text()
    assert _section_failure_marker(body) == "data pull failed"


def test_all_ticket_markers_have_fixtures() -> None:
    """Every marker in SECTION_FAILURE_MARKERS has a synthetic fixture, so
    extending the marker list without extending the fixtures fails CI."""
    fixture_markers = set(FAILURE_FIXTURES.values())
    assert set(SECTION_FAILURE_MARKERS) == fixture_markers, (
        "marker list and fixture coverage drifted; "
        f"in markers not fixtures: {set(SECTION_FAILURE_MARKERS) - fixture_markers}; "
        f"in fixtures not markers: {fixture_markers - set(SECTION_FAILURE_MARKERS)}"
    )


def test_empty_section_returns_none() -> None:
    """The predicate itself returns None for empty input. The stage handler
    treats empty bodies as failures separately (an empty section file is
    its own failure mode); this test pins predicate behaviour, not stage
    behaviour."""
    assert _section_failure_marker("") is None
    assert _section_failure_marker("   \n  ") is None


def test_historical_pass_rate_documented() -> None:
    """The reference doc records the expected 0/4 pass rate against the
    last four shipped reports. Pin its existence so the reference doesn't
    silently disappear in a refactor."""
    doc = FIXTURE_DIR / "historical_failures.md"
    assert doc.exists(), "historical_failures.md reference doc missing"
    text = doc.read_text()
    assert "0/4" in text
    # Every ticketed marker should be named in the reference doc.
    for marker in SECTION_FAILURE_MARKERS:
        assert marker in text, f"reference doc missing marker {marker!r}"
