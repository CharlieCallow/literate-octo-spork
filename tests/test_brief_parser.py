"""Brief parser tests. The EIC's structured brief is parsed by regex; these
make sure the regexes don't drift when prompts get tuned."""

from __future__ import annotations

from api.workflow.state_machine import parse_brief

SAMPLE = """# ANGLE
The duration trade is broken; dispersion is just starting.

# SUBTITLE
A look at how macro plays out at the security level

# QUESTIONS
1. What's consensus on rates?
2. Where is consensus most fragile?

# CONTRIBUTORS
- `macro-strategist`: rate path and central-bank reaction function
- `equity-analyst`: high-beta names exposed to dispersion

# CHARTS
- 10Y yield with policy rate overlay
- SPX dispersion vs realised vol

# DATA SOURCES
- FRED: DGS10, DFF
- yfinance: SPY, ^VIX

# STRUCTURE
- Macro frame
- Equity exposure
- Risk register
"""


def test_extracts_contributors() -> None:
    parsed = parse_brief(SAMPLE)
    assert parsed["contributor_slugs"] == ["macro-strategist", "equity-analyst"]


def test_extracts_subtitle() -> None:
    parsed = parse_brief(SAMPLE)
    assert parsed["subtitle"] == "A look at how macro plays out at the security level"


def test_extracts_angle() -> None:
    parsed = parse_brief(SAMPLE)
    assert "duration trade" in str(parsed["angle"])


def test_dedupe_contributors() -> None:
    text = """# CONTRIBUTORS
- `macro-strategist`: x
- `macro-strategist`: y
- `equity-analyst`: z
"""
    parsed = parse_brief(text)
    assert parsed["contributor_slugs"] == ["macro-strategist", "equity-analyst"]


def test_missing_sections_default_to_empty() -> None:
    parsed = parse_brief("# ANGLE\nJust the angle.\n")
    assert parsed["contributor_slugs"] == []
    assert parsed["subtitle"] == ""
    assert "Just the angle" in str(parsed["angle"])


def test_parses_adhoc_specialists() -> None:
    text = """# AD-HOC SPECIALIST
- `clinical-trials-analyst`: covers Phase 2/3 readouts and biotech pipeline flow
- `power-grid-analyst`: covers PJM/ERCOT interconnect queue dynamics
"""
    parsed = parse_brief(text)
    specs = parsed["adhoc_specialists"]
    assert len(specs) == 2  # type: ignore[arg-type]
    assert specs[0]["slug"] == "clinical-trials-analyst"  # type: ignore[index]
    assert "Phase 2/3" in specs[0]["request"]  # type: ignore[index]
    assert specs[1]["slug"] == "power-grid-analyst"  # type: ignore[index]


def test_adhoc_none_treated_as_empty() -> None:
    text = "# AD-HOC SPECIALIST\n(none)\n"
    parsed = parse_brief(text)
    assert parsed["adhoc_specialists"] == []


def test_adhoc_section_missing_yields_empty() -> None:
    parsed = parse_brief("# ANGLE\nThe angle.\n")
    assert parsed["adhoc_specialists"] == []
