"""Scout digest parser tests."""

from __future__ import annotations

from api.scout_parser import parse_themes

SAMPLE = """# THEME 1
**headline:** Power-grid bottleneck behind the AI capex story
**why_now:** Three utility CEOs mentioned interconnect queues this week
**dig_into:** PJM/ERCOT queue data; Vistra/Constellation guidance
**sources:** https://example.com/a, https://example.com/b

# THEME 2
**headline:** Japan reflation, this time with wage data
**why_now:** Annual shunto ended with the highest base-pay rise since 1991
**dig_into:** Industrial unions; BoJ governor speeches; TOPIX vs Nikkei spread
**sources:** https://example.com/jp1

# THEME 3
**headline:** Small-caps narrative is finished
**why_now:** Three desks rebranded the same trade in ten days
**dig_into:** IWM relative to SPX; sell-side note dates
**sources:**
"""


def test_parses_three_themes() -> None:
    out = parse_themes(SAMPLE)
    assert len(out) == 3


def test_extracts_fields() -> None:
    out = parse_themes(SAMPLE)
    assert out[0].headline.startswith("Power-grid")
    assert "interconnect queues" in out[0].why_now
    assert "PJM" in out[0].dig_into


def test_extracts_urls() -> None:
    out = parse_themes(SAMPLE)
    assert out[0].source_urls == ["https://example.com/a", "https://example.com/b"]
    assert out[1].source_urls == ["https://example.com/jp1"]
    assert out[2].source_urls == []


def test_skips_block_with_no_headline() -> None:
    text = """# THEME 1
**why_now:** something

# THEME 2
**headline:** Real one
**why_now:** stuff
"""
    out = parse_themes(text)
    assert len(out) == 1
    assert out[0].headline == "Real one"


def test_empty_on_no_match() -> None:
    assert parse_themes("just some prose") == []
