"""Tests for the editable `draft.md` artifact.

Acceptance criterion from the ticket: a human can fix a typo in draft.md
in a text editor, run the render command, and get a corrected PDF in
under a minute with no LLM cost. Round-trip (dump -> load) must be
lossless on every field that drives the cover, body, callouts, glossary,
positions, sources, and per-section attribution. Edits to prose must
flow through to the PDF; charts referenced as `[chart: name.png]` must
not be regenerated."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from api.render.draft import (
    Draft,
    dump_draft,
    figures_to_chart_tags,
    inline_chart_tags,
    load_draft,
    render_draft,
    render_from_dir,
)
from api.render.pdf import Contributor, Section


def _sample_draft() -> Draft:
    return Draft(
        title="The Dispersion Trade",
        subtitle="Why the index hides a wider spread",
        date="2026-05-01",
        contributors=[
            Contributor("Margaux Devlin", "Editor-in-Chief"),
            Contributor("Henrik Voss", "Macro Strategist"),
        ],
        sections=[
            Section(heading="Opening", body_md="Index vol low. Dispersion not."),
            Section(
                heading="Macro view",
                body_md="**Base case:** curve done.\n\n[chart: rates.png]\n\nMore prose.",
                author="Henrik Voss",
                role="Macro Strategist",
            ),
            Section(heading="Closing", body_md="Watch the realised-vs-implied gap."),
        ],
        house_view_top="Trade the dispersion, not the index.",
        house_view_bottom="Long dispersion, short the average.",
        disagreement="Henrik thinks the curve has more to give.",
        bear_case="If realised vol catches up, the trade unwinds in a week.",
        glossary="- **Dispersion** -- spread of single-stock returns around the index.\n",
        positions=[
            {"asset": "SPX", "direction": "short", "horizon_days": 90,
             "target": 5200.0, "conviction": 4},
            {"asset": "TSM", "direction": "long", "horizon_days": 180,
             "target": None, "conviction": 5},
        ],
        sources=[
            {"n": 1, "url": "https://fred.stlouisfed.org/series/DGS10",
             "title": "10Y", "source": "FRED"},
        ],
        read_minutes=10,
        hide_bylines=False,
        hide_positions=False,
        hide_disclosures=False,
    )


def test_round_trip_is_lossless(tmp_path: Path) -> None:
    """dump -> load should preserve every field. This is the contract:
    if it isn't lossless, edits to draft.md will silently drop data
    on the next render."""
    original = _sample_draft()
    dump_draft(original, tmp_path)
    loaded = load_draft(tmp_path)

    assert loaded.title == original.title
    assert loaded.subtitle == original.subtitle
    assert loaded.date == original.date
    assert loaded.read_minutes == original.read_minutes
    assert loaded.hide_bylines == original.hide_bylines
    assert loaded.hide_positions == original.hide_positions
    assert loaded.hide_disclosures == original.hide_disclosures
    assert [(c.name, c.role) for c in loaded.contributors] == \
        [(c.name, c.role) for c in original.contributors]
    assert loaded.positions == original.positions
    assert loaded.sources == original.sources
    assert loaded.house_view_top == original.house_view_top
    assert loaded.house_view_bottom == original.house_view_bottom
    assert loaded.disagreement == original.disagreement
    assert loaded.bear_case == original.bear_case
    assert (loaded.glossary or "").strip() == (original.glossary or "").strip()

    assert len(loaded.sections) == len(original.sections)
    for got, want in zip(loaded.sections, original.sections, strict=True):
        assert got.heading == want.heading
        assert got.author == want.author
        assert got.role == want.role
        assert got.body_md.strip() == want.body_md.strip()


def test_human_edit_to_prose_survives_round_trip(tmp_path: Path) -> None:
    """The whole point: edit draft.md by hand, reload, edits stick.
    Simulates the user fixing a typo in the macro section."""
    dump_draft(_sample_draft(), tmp_path)
    text = (tmp_path / "draft.md").read_text(encoding="utf-8")
    edited = text.replace("Base case:", "Base case (revised):")
    assert edited != text
    (tmp_path / "draft.md").write_text(edited, encoding="utf-8")

    loaded = load_draft(tmp_path)
    macro = next(s for s in loaded.sections if s.heading == "Macro view")
    assert "Base case (revised):" in macro.body_md


def test_chart_tag_round_trip() -> None:
    """`[chart: x.png]` <-> `<figure><img>` must round-trip so the
    workflow can hand us inlined chart blobs and we can store the
    human-friendly shorthand without losing the reference."""
    body = "intro\n\n[chart: rates.png]\n\nbridge\n\n[chart: cpi.png]\n\nend"
    # Simulate the workflow's already-expanded body.
    expanded = body.replace(
        "[chart: rates.png]",
        '<figure><img src="file:///tmp/wd/charts/rates.png" alt="rates.png"></figure>',
    ).replace(
        "[chart: cpi.png]",
        '<figure><img src="file:///tmp/wd/charts/cpi.png" alt="cpi.png"></figure>',
    )
    collapsed = figures_to_chart_tags(expanded)
    assert "[chart: rates.png]" in collapsed
    assert "[chart: cpi.png]" in collapsed
    assert "<figure>" not in collapsed


def test_render_does_not_regenerate_charts(tmp_path: Path) -> None:
    """Editing prose must not re-run matplotlib. The renderer reads
    chart PNGs from disk -- it does not import matplotlib at all."""
    charts = tmp_path / "charts"
    charts.mkdir()
    chart_path = charts / "rates.png"
    chart_path.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    chart_mtime_before = chart_path.stat().st_mtime_ns

    draft = Draft(
        title="t", subtitle="s", date="2026-05-01",
        sections=[Section(heading="X", body_md="see [chart: rates.png]")],
    )

    captured: dict[str, object] = {}

    def fake_render_pdf(**kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        out = kwargs["out_path"]
        Path(out).write_bytes(b"%PDF-fake")
        return Path(out)

    with patch("api.render.draft.render_pdf", side_effect=fake_render_pdf):
        out = render_draft(draft, charts, tmp_path / "report.pdf")

    assert out.exists()
    assert chart_path.stat().st_mtime_ns == chart_mtime_before, "chart PNG was rewritten"

    # Chart tag was expanded to a <figure> pointing at the on-disk file.
    sections = list(captured["sections"])  # type: ignore[arg-type]
    body = sections[0].body_md
    assert "<figure>" in body
    assert chart_path.as_uri() in body


def test_render_from_dir_uses_drafts_md(tmp_path: Path) -> None:
    """Integration: dump a Draft, render via the CLI entry point,
    confirm render_pdf is called with the loaded data."""
    dump_draft(_sample_draft(), tmp_path)
    (tmp_path / "charts").mkdir()

    captured: dict[str, object] = {}

    def fake_render_pdf(**kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        Path(kwargs["out_path"]).write_bytes(b"%PDF-fake")
        return Path(kwargs["out_path"])

    with patch("api.render.draft.render_pdf", side_effect=fake_render_pdf):
        render_from_dir(tmp_path)

    assert captured["title"] == "The Dispersion Trade"
    assert captured["disagreement"] == "Henrik thinks the curve has more to give."
    assert len(list(captured["sections"])) == 3  # type: ignore[arg-type]
    pos = list(captured["positions"])  # type: ignore[arg-type]
    assert pos[0]["asset"] == "SPX"


def test_inline_chart_tags_drops_missing_files(tmp_path: Path) -> None:
    """Missing chart files render as nothing, not as a placeholder.
    A wrong chart reads worse than no chart."""
    charts = tmp_path / "charts"
    charts.mkdir()
    (charts / "real.png").write_bytes(b"x")

    body = "see [chart: real.png] and [chart: ghost.png]"
    out = inline_chart_tags(body, charts)
    assert "real.png" in out
    assert "ghost.png" not in out


def test_render_skipped_when_glossary_absent(tmp_path: Path) -> None:
    """Empty glossary should not emit a `# GLOSSARY` block to draft.md.
    Pins the dump's behaviour for optional fields."""
    d = _sample_draft()
    d.glossary = None
    d.disagreement = None
    dump_draft(d, tmp_path)
    text = (tmp_path / "draft.md").read_text(encoding="utf-8")
    assert "# GLOSSARY" not in text
    assert "# DISAGREEMENT" not in text
    # Required structural block still present.
    assert "# SECTIONS" in text


# Touch unused imports so ruff/mypy don't flag them when running this file
# in isolation.
_ = MagicMock
