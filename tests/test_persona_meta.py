"""Persona metadata + path resolution tests for ad-hoc specialists.

Standing roster slugs resolve from ROSTER. Temp slugs are read from the
'# Name -- Role' header line of team/temp/<slug>.md."""

from __future__ import annotations

import pytest

from api.workflow.state_machine import _persona_meta, _persona_path


@pytest.fixture
def team_dirs(monkeypatch, tmp_path):  # type: ignore[no-untyped-def]
    """Point settings.team_dir at a tmp directory and seed temp/."""
    standing = tmp_path / "team"
    temp = standing / "temp"
    standing.mkdir()
    temp.mkdir()

    # Seed a fake standing persona (not actually used here -- meta short-circuits via ROSTER)
    (standing / "macro-strategist.md").write_text("# Henrik Voss — Macro Strategist\n", encoding="utf-8")

    import api.settings as settings_module
    monkeypatch.setattr(type(settings_module.settings), "team_dir", property(lambda self: standing))
    yield standing, temp


def test_standing_slug_uses_roster(team_dirs) -> None:  # type: ignore[no-untyped-def]
    meta = _persona_meta("macro-strategist")
    assert meta is not None
    assert meta["name"] == "Henrik Voss"
    assert meta["role"] == "Macro Strategist"


def test_temp_slug_reads_header(team_dirs) -> None:  # type: ignore[no-untyped-def]
    _, temp = team_dirs
    (temp / "clinical-trials-analyst.md").write_text(
        "# Dr. Lina Hart — Clinical Trials Analyst\n\n**Bio.** Ex-FDA biostatistician.",
        encoding="utf-8",
    )
    meta = _persona_meta("clinical-trials-analyst")
    assert meta is not None
    assert meta["name"] == "Dr. Lina Hart"
    assert meta["role"] == "Clinical Trials Analyst"


def test_unknown_slug_returns_none(team_dirs) -> None:  # type: ignore[no-untyped-def]
    assert _persona_meta("nobody") is None


def test_persona_path_finds_temp(team_dirs) -> None:  # type: ignore[no-untyped-def]
    _, temp = team_dirs
    p = temp / "x.md"
    p.write_text("# X — Y", encoding="utf-8")
    found = _persona_path("x")
    assert found is not None
    assert found.resolve() == p.resolve()


def test_temp_with_dashes_for_emdash(team_dirs) -> None:  # type: ignore[no-untyped-def]
    """Some markdown editors convert em-dash to '--'; both should parse."""
    _, temp = team_dirs
    (temp / "ascii-only.md").write_text("# Plain Name -- Plain Role\n", encoding="utf-8")
    meta = _persona_meta("ascii-only")
    assert meta is not None
    assert meta["name"] == "Plain Name"
    assert meta["role"] == "Plain Role"
