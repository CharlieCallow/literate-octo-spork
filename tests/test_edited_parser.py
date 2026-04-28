"""parse_edited() splits the EIC's revised-sections markdown into structured
fields. Regression tests so the chart/section pipeline doesn't silently break."""

from __future__ import annotations

from api.workflow.state_machine import parse_edited

SAMPLE = """# OPENING
Markets keep telling you nuclear is back. Markets are right.

# HOUSE VIEW (TOP)
Long the bottleneck, not the pounds.

# REVISED SECTIONS
## Macro view
**author:** Henrik Voss
**role:** Macro Strategist

The Western fuel cycle is short of conversion and enrichment.

## Equity exposure
**author:** Priya Anand
**role:** Equity / Sector Analyst

Cameco at 2.4x book is not where the value lives anymore.

# HOUSE VIEW (BOTTOM)
Buy the fuel cycle, sell the headline.

# CLOSING
Watch the political consensus.
"""


def test_opening_extracted() -> None:
    parsed = parse_edited(SAMPLE)
    assert "Markets keep telling you" in str(parsed["opening"])


def test_house_views_extracted() -> None:
    parsed = parse_edited(SAMPLE)
    assert parsed["house_view_top"] == "Long the bottleneck, not the pounds."
    assert parsed["house_view_bottom"] == "Buy the fuel cycle, sell the headline."


def test_sections_in_order() -> None:
    parsed = parse_edited(SAMPLE)
    sections = parsed["sections"]
    assert len(sections) == 2  # type: ignore[arg-type]
    assert sections[0]["heading"] == "Macro view"  # type: ignore[index]
    assert sections[0]["author"] == "Henrik Voss"  # type: ignore[index]
    assert sections[0]["role"] == "Macro Strategist"  # type: ignore[index]
    assert "fuel cycle" in sections[0]["body"]  # type: ignore[index]
    assert sections[1]["heading"] == "Equity exposure"  # type: ignore[index]
    assert sections[1]["author"] == "Priya Anand"  # type: ignore[index]


def test_closing_extracted() -> None:
    parsed = parse_edited(SAMPLE)
    assert "political consensus" in str(parsed["closing"])


# Same shape but with the new editorial blocks the EIC now emits.
SAMPLE_WITH_BEAR_AND_DISAGREEMENT = """# OPENING
Markets keep telling you nuclear is back. Markets are right.

# HOUSE VIEW (TOP)
Long the bottleneck, not the pounds.

# REVISED SECTIONS
## Macro view
**author:** Henrik Voss
**role:** Macro Strategist

The Western fuel cycle is short of conversion and enrichment.

## Equity exposure
**author:** Priya Anand
**role:** Equity / Sector Analyst

Cameco at 2.4x book is not where the value lives anymore.

# DISAGREEMENT
Henrik likes the conversion bottleneck; Priya thinks the equity is a financing trap.

# BEAR CASE
Where we'd be wrong: a Kazakh swing back online clears the bottleneck.

# HOUSE VIEW (BOTTOM)
Buy the fuel cycle, sell the headline.

# CLOSING
Watch the political consensus.
"""


def test_disagreement_block_extracted() -> None:
    parsed = parse_edited(SAMPLE_WITH_BEAR_AND_DISAGREEMENT)
    assert "financing trap" in str(parsed["disagreement"])


def test_bear_case_block_extracted() -> None:
    parsed = parse_edited(SAMPLE_WITH_BEAR_AND_DISAGREEMENT)
    assert "Kazakh swing" in str(parsed["bear_case"])


def test_disagreement_none_means_empty() -> None:
    """`(none)` is the literal the EIC writes when there's no real
    disagreement. The renderer treats empty as 'skip the callout'."""
    text = SAMPLE_WITH_BEAR_AND_DISAGREEMENT.replace(
        "Henrik likes the conversion bottleneck; Priya thinks the equity is a financing trap.",
        "(none)",
    )
    parsed = parse_edited(text)
    assert parsed["disagreement"] == ""


def test_revised_sections_dont_swallow_following_blocks() -> None:
    """Regression: with new # DISAGREEMENT / # BEAR CASE blocks between
    REVISED SECTIONS and HOUSE VIEW (BOTTOM), the section parser must not
    pull them into the analyst sections."""
    parsed = parse_edited(SAMPLE_WITH_BEAR_AND_DISAGREEMENT)
    sections = parsed["sections"]
    assert len(sections) == 2  # type: ignore[arg-type]
    for s in sections:  # type: ignore[union-attr]
        body = s["body"]  # type: ignore[index]
        assert "DISAGREEMENT" not in body
        assert "BEAR CASE" not in body
        assert "HOUSE VIEW" not in body
