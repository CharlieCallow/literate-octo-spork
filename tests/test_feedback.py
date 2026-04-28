"""Feedback log appender tests. Persona files accumulate dated notes under
'## Feedback log'; oldest entries get evicted past MAX_ENTRIES."""

from __future__ import annotations

from pathlib import Path

import pytest

from api.feedback import MAX_ENTRIES, append_entry, append_to_persona


def _make_persona(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "test-persona.md"
    p.write_text(body, encoding="utf-8")
    return p


def test_appends_under_existing_log_section(tmp_path: Path) -> None:
    p = _make_persona(tmp_path, """# Test

Bio bla bla.

## Feedback log

_No reports yet._
""")
    append_entry(p, body="Worked well: the punchy take.", report_id=42)
    text = p.read_text(encoding="utf-8")
    assert "## Feedback log" in text
    assert "_No reports yet._" not in text
    assert "report 42" in text
    assert "Worked well" in text


def test_creates_log_section_if_missing(tmp_path: Path) -> None:
    p = _make_persona(tmp_path, "# Test\n\nBio.\n")
    append_entry(p, body="First note.", report_id=1)
    text = p.read_text(encoding="utf-8")
    assert "## Feedback log" in text
    assert "report 1" in text


def test_caps_at_max_entries(tmp_path: Path) -> None:
    p = _make_persona(tmp_path, "# Test\n\n## Feedback log\n\n")
    for i in range(MAX_ENTRIES + 5):
        append_entry(p, body=f"note number {i}", report_id=i)
    text = p.read_text(encoding="utf-8")
    # Oldest 5 should be gone; newest 10 should remain.
    assert "report 0" not in text
    assert "report 4" not in text
    assert "report 5" in text
    assert f"report {MAX_ENTRIES + 4}" in text
    # Count entries
    assert text.count("### ") == MAX_ENTRIES


def test_preserves_pre_log_content(tmp_path: Path) -> None:
    body = "# Test\n\nIntro line.\n\n**Voice.**\n- Punchy.\n\n## Feedback log\n\n_No reports yet._\n"
    p = _make_persona(tmp_path, body)
    append_entry(p, body="A note.", report_id=7)
    text = p.read_text(encoding="utf-8")
    # Pre-log content survives.
    assert "Intro line" in text
    assert "**Voice.**" in text
    assert "- Punchy." in text


@pytest.fixture
def db_env(monkeypatch, tmp_path):  # type: ignore[no-untyped-def]
    """Isolated DB + team dir for slug-based persona feedback tests."""
    from sqlmodel import SQLModel, create_engine

    eng = create_engine(f"sqlite:///{tmp_path}/fb.db", connect_args={"check_same_thread": False})

    standing = tmp_path / "team"
    temp = standing / "temp"
    archive = standing / "archive"
    standing.mkdir()
    temp.mkdir()
    archive.mkdir()
    (standing / "macro-strategist.md").write_text(
        "# Henrik Voss — Macro Strategist\n\n**Bio.** ex-Nordea.\n\n## Feedback log\n\n_No reports yet._\n",
        encoding="utf-8",
    )
    (temp / "clinical-trials-analyst.md").write_text(
        "# Lina Hart — Clinical Trials Analyst\n\n**Bio.** ex-NEJM.\n\n## Feedback log\n\n_No reports yet._\n",
        encoding="utf-8",
    )

    import api.db as db_module
    import api.personas as personas_module
    from api import models  # noqa: F401

    monkeypatch.setattr(db_module, "engine", eng)
    monkeypatch.setattr(personas_module, "engine", eng)
    import api.settings as settings_module
    monkeypatch.setattr(type(settings_module.settings), "team_dir", property(lambda self: standing))

    SQLModel.metadata.create_all(eng)
    from api.personas import seed_from_filesystem
    seed_from_filesystem()
    yield eng, standing, temp


def test_append_to_persona_writes_to_db(db_env) -> None:  # type: ignore[no-untyped-def]
    """Feedback must land in the DB row, not just on disk -- otherwise
    hydrate_filesystem on the next deploy wipes it."""
    from api.personas import get
    assert append_to_persona("macro-strategist", body="Sharper Fed take.", report_id=11) is True
    row = get("macro-strategist")
    assert row is not None
    assert "Sharper Fed take." in row.markdown
    assert "report 11" in row.markdown


def test_append_to_persona_survives_redeploy(db_env) -> None:  # type: ignore[no-untyped-def]
    """Simulate a Railway rebuild: wipe team/ and re-hydrate from DB.
    Feedback must come back."""
    _, standing, _ = db_env
    append_to_persona("macro-strategist", body="Survived rebuild.", report_id=99)
    # Wipe disk like a fresh container.
    for p in standing.rglob("*.md"):
        p.unlink()
    from api.personas import hydrate_filesystem
    hydrate_filesystem()
    text = (standing / "macro-strategist.md").read_text(encoding="utf-8")
    assert "Survived rebuild." in text
    assert "report 99" in text


def test_append_to_persona_handles_temp_specialist(db_env) -> None:  # type: ignore[no-untyped-def]
    """Temp specialists live in team/temp/<slug>.md. The slug-based appender
    should find them via the DB regardless of where they sit on disk."""
    _, _, temp = db_env
    assert append_to_persona("clinical-trials-analyst", body="Solid Phase 2 read.", report_id=3) is True
    text = (temp / "clinical-trials-analyst.md").read_text(encoding="utf-8")
    assert "Solid Phase 2 read." in text


def test_append_to_persona_returns_false_for_unknown_slug(db_env) -> None:  # type: ignore[no-untyped-def]
    assert append_to_persona("nobody", body="ignored", report_id=1) is False
