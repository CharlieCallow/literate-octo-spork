"""Inline citation transformer tests."""

from __future__ import annotations

from api.citations import attach_inline_citations
from api.render.pdf import Section


def _section(body: str, heading: str = "Test") -> Section:
    return Section(heading=heading, body_md=body)


def test_replaces_link_with_anchored_superscript() -> None:
    s = _section("Spot uranium up 220% off lows ([Cameco](https://example.com/q3)).")
    out, sources = attach_inline_citations([s], None, check_urls=False)
    assert "[1]" in out[0].body_md
    assert 'href="#cite-1"' in out[0].body_md
    # Anchor text remains visible
    assert "Cameco" in out[0].body_md
    assert sources == [{"n": "1", "url": "https://example.com/q3", "title": "Cameco", "source": "inline"}]


def test_dedupes_repeated_url() -> None:
    s = _section("First ([Source](https://a.com/x)), and again ([Source](https://a.com/x)).")
    out, sources = attach_inline_citations([s], None, check_urls=False)
    # Both citations should be [1]
    assert out[0].body_md.count('href="#cite-1"') == 2
    assert len(sources) == 1


def test_assigns_sequential_numbers_across_sections() -> None:
    s1 = _section("Claim ([A](https://a.com/x)).")
    s2 = _section("Other claim ([B](https://b.com/y)).")
    out, sources = attach_inline_citations([s1, s2], None, check_urls=False)
    assert 'href="#cite-1"' in out[0].body_md
    assert 'href="#cite-2"' in out[1].body_md
    assert [s["n"] for s in sources] == ["1", "2"]


def test_appends_uncited_extras_after_inline() -> None:
    s = _section("Inline ([A](https://a.com/x)).")
    extras = [
        {"url": "https://a.com/x", "title": "Already inline", "source": "web"},
        {"url": "https://b.com/y", "title": "Web search hit", "source": "web"},
    ]
    out, sources = attach_inline_citations([s], extras, check_urls=False)
    _ = out  # silence unused-var lint
    # a.com/x keeps n=1 (inline), b.com/y gets n=2 (uncited tail)
    assert sources[0]["n"] == "1"
    assert sources[0]["url"] == "https://a.com/x"
    assert sources[1]["n"] == "2"
    assert sources[1]["url"] == "https://b.com/y"


def test_strips_trailing_punctuation_from_url() -> None:
    s = _section("See ([the link](https://example.com/path).)")
    _, sources = attach_inline_citations([s], None, check_urls=False)
    assert sources[0]["url"] == "https://example.com/path"


def test_drops_unreachable_urls_from_sources(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Citations to URLs that fail the reachability check stay as visible
    anchor text (no superscript) and don't appear in the Sources list."""
    import api.citations as citations_module

    def fake_check(urls: list[str]) -> set[str]:
        return {u for u in urls if "good" in u}

    monkeypatch.setattr(citations_module, "check_reachable", fake_check)

    s = _section(
        "Working ([Cameco](https://good.example.com/q3)) and "
        "broken ([gone](https://bad.example.com/dead))."
    )
    out, sources = attach_inline_citations([s], None, check_urls=True)
    assert 'href="#cite-1"' in out[0].body_md  # working link cited
    assert "gone" in out[0].body_md            # anchor text survives
    assert "https://bad.example.com" not in out[0].body_md  # but no link
    assert len(sources) == 1                    # Sources only has the good one
    assert sources[0]["url"] == "https://good.example.com/q3"


def test_leaves_chart_tags_alone() -> None:
    s = _section("[chart: rates.png]\n\nCommentary.")
    out, sources = attach_inline_citations([s], None, check_urls=False)
    # chart tags don't have an http URL inside the parens
    assert "[chart: rates.png]" in out[0].body_md
    assert sources == []


def test_drops_generic_homepage_citations() -> None:
    """Bare-homepage URLs (no path, e.g. https://www.federalreserve.gov/)
    aren't real citations -- we strip them like dead links so the macro
    section can't pad Sources with publisher home pages."""
    s = _section(
        "Real cite ([FRED](https://fred.stlouisfed.org/series/DGS10)), "
        "lazy cite ([Fed](https://www.federalreserve.gov/)), "
        "another lazy ([OPEC](https://www.opec.org))."
    )
    out, sources = attach_inline_citations([s], None, check_urls=False)
    body = out[0].body_md
    assert 'href="#cite-1"' in body  # real link cited
    assert "Fed" in body              # anchor text survives
    assert "OPEC" in body
    assert "federalreserve.gov" not in body  # but no link
    assert "opec.org" not in body
    assert len(sources) == 1
    assert sources[0]["url"] == "https://fred.stlouisfed.org/series/DGS10"


def test_drops_generic_homepage_from_extras() -> None:
    """Web-search hits that are bare homepages also get filtered from the
    uncited Sources tail."""
    s = _section("No inline cites here.")
    extras = [
        {"url": "https://www.opec.org/", "title": "OPEC", "source": "web"},
        {"url": "https://www.opec.org/about_us/our-mission.htm", "title": "OPEC Mission", "source": "web"},
    ]
    _, sources = attach_inline_citations([s], extras, check_urls=False)
    urls = [s["url"] for s in sources]
    assert "https://www.opec.org/" not in urls
    assert "https://www.opec.org/about_us/our-mission.htm" in urls
