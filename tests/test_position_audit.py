"""Position-audit reconciliation: every cover ticker must be defended in
an analyst section with a recommendation verb that matches the table
direction. Category-level argument is not enough -- ticker-level defense
is the bar."""

from __future__ import annotations

from api.workflow.state_machine import (
    _ticker_defended,
    _ticker_mentioned,
    _ticker_mention_re,
)


def _section(body: str) -> dict[str, object]:
    return {"heading": "Section", "author": "A", "role": "analyst", "body": body}


def test_long_ticker_named_and_recommended_passes() -> None:
    sections = [_section("We're long ASE on the substrate cycle.")]
    assert _ticker_defended("ASE", "long", sections)


def test_short_ticker_named_and_recommended_passes() -> None:
    sections = [_section("Short NVDA on the comps -- the GM print won't hold.")]
    assert _ticker_defended("NVDA", "short", sections)


def test_avoid_ticker_named_and_recommended_passes() -> None:
    sections = [_section("Avoid TSLA into the print; the deliveries miss is set.")]
    assert _ticker_defended("TSLA", "avoid", sections)


def test_ticker_named_without_matching_verb_fails() -> None:
    # The ASE/AMKR failure mode: cover says "long ASE", body merely names
    # ASE as substrate exposure but never argues for it as a long.
    sections = [_section("Substrate exposure runs through ASE and AMKR.")]
    assert not _ticker_defended("ASE", "long", sections)


def test_ticker_absent_from_sections_fails() -> None:
    sections = [_section("We're long the substrate complex.")]
    assert not _ticker_defended("ASE", "long", sections)
    assert not _ticker_mentioned("ASE", sections)


def test_direction_mismatch_fails() -> None:
    # Body argues a short, table claims a long -- direction must match.
    sections = [_section("Short ASE -- the package lead times are rolling over.")]
    assert not _ticker_defended("ASE", "long", sections)


def test_window_keeps_verb_within_two_sentences() -> None:
    # Verb sits within ~one sentence; defense passes.
    near = [_section("Long ASE here. The substrate cycle is early.")]
    assert _ticker_defended("ASE", "long", near)
    # Same ticker, but the verb sits paragraphs away with unrelated prose
    # in between -- defense should fail.
    far_body = (
        "ASE is the substrate-exposure name we keep coming back to. "
        + ("Lorem ipsum dolor sit amet. " * 30)
        + "We're long the cycle."
    )
    assert not _ticker_defended("ASE", "long", [_section(far_body)])


def test_ticker_not_matched_inside_longer_word() -> None:
    # ASE shouldn't match inside `LASER`.
    pattern = _ticker_mention_re("ASE")
    assert not pattern.search("LASER readout")
    assert pattern.search("ASE prints next week")


def test_ticker_mentioned_finds_clean_mention() -> None:
    sections = [_section("AMKR sits at the seam of test and OSAT.")]
    assert _ticker_mentioned("AMKR", sections)


def test_unknown_direction_fails_safely() -> None:
    # `hold` isn't a valid table direction (the extractor drops holds);
    # an audit query for it must always fail.
    sections = [_section("We're long ASE on the substrate cycle.")]
    assert not _ticker_defended("ASE", "hold", sections)
