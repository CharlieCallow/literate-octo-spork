"""Title/subtitle template enforcement.

The cover ships a 2-6 word headline and a 4-12 word subtitle, neither
ending in a terminal period. The render stage validates against this
contract and re-prompts the EIC on rejection. These tests pin the
validator and the parsing of the EIC's retitle response.
"""

from __future__ import annotations

from api.workflow.state_machine import (
    _mechanical_clean_title,
    _parse_retitle,
    parse_brief,
    validate_title_subtitle,
)


def test_passes_for_canonical_examples() -> None:
    # The four "good" examples from the ticket / persona file.
    assert validate_title_subtitle("Oil Flash Note", "The illusion of plenty") == []
    assert validate_title_subtitle(
        "The CPO Trade",
        "Why the substrate, not the transceiver, captures the interconnect transition",
    ) == []
    assert validate_title_subtitle(
        "SpaceX Play",
        "Hyperscalers are paying scarcity rent for 1970s reactors",
    ) == []
    assert validate_title_subtitle("The SMR Obituary", "The illusion of plenty") == []


def test_rejects_long_title() -> None:
    reasons = validate_title_subtitle(
        "Optical interconnect just became the new I/O wall",
        "good subtitle phrase here",
    )
    assert any("title has" in r and "cap is 6" in r for r in reasons)


def test_rejects_short_title() -> None:
    reasons = validate_title_subtitle("Solo", "good subtitle phrase here")
    assert any("at least 2" in r for r in reasons)


def test_rejects_terminal_period_on_title() -> None:
    reasons = validate_title_subtitle("The CPO Trade.", "good subtitle phrase here")
    assert any("title ends with a period" in r for r in reasons)


def test_rejects_terminal_period_on_subtitle() -> None:
    reasons = validate_title_subtitle(
        "The CPO Trade", "The illusion of plenty."
    )
    assert any("subtitle ends with a period" in r for r in reasons)


def test_rejects_long_subtitle() -> None:
    reasons = validate_title_subtitle(
        "The CPO Trade",
        "this subtitle goes on and on for far too many words to count clearly",
    )
    assert any("cap is 12" in r for r in reasons)


def test_rejects_short_subtitle() -> None:
    reasons = validate_title_subtitle("The CPO Trade", "too short")
    assert any("at least 4" in r for r in reasons)


def test_regression_set_word_counts() -> None:
    # The four shipped reports had title word counts 2, 11, 26, 17.
    # Only the first should pass the title rule.
    counts = [
        ("AB " * 2, False),    # 2 words -- but min subtitle below; check title only
        ("AB " * 11, True),    # 11 words -- fails
        ("AB " * 26, True),    # 26 words -- fails
        ("AB " * 17, True),    # 17 words -- fails
    ]
    sub_ok = "the illusion of plenty"
    for title, expect_title_failure in counts:
        reasons = validate_title_subtitle(title.strip(), sub_ok)
        title_failure = any("title has" in r and "cap" in r for r in reasons)
        assert title_failure is expect_title_failure, (title, reasons)


def test_mechanical_clean_truncates_and_strips_period() -> None:
    t, s = _mechanical_clean_title(
        "This is a really long title with too many words.",
        "A subtitle that ends in a period.",
    )
    assert t == "This is a really long title"
    assert not t.endswith(".")
    assert not s.endswith(".")
    assert len(t.split()) <= 6
    assert len(s.split()) <= 12


def test_parse_brief_extracts_title() -> None:
    text = """# ANGLE
some angle

# TITLE
The CPO Trade

# SUBTITLE
Why the substrate captures the interconnect transition
"""
    parsed = parse_brief(text)
    assert parsed["title"] == "The CPO Trade"
    assert parsed["subtitle"] == "Why the substrate captures the interconnect transition"


def test_parse_retitle_response() -> None:
    text = """# TITLE
The CPO Trade

# SUBTITLE
Why the substrate captures the interconnect transition
"""
    title, subtitle = _parse_retitle(text)
    assert title == "The CPO Trade"
    assert subtitle == "Why the substrate captures the interconnect transition"


def test_parse_retitle_strips_wrapping_quotes() -> None:
    text = """# TITLE
"The CPO Trade"

# SUBTITLE
'Why the substrate captures the interconnect transition'
"""
    title, subtitle = _parse_retitle(text)
    assert title == "The CPO Trade"
    assert subtitle == "Why the substrate captures the interconnect transition"
