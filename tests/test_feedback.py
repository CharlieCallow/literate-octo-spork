"""Feedback log appender tests. Persona files accumulate dated notes under
'## Feedback log'; oldest entries get evicted past MAX_ENTRIES."""

from __future__ import annotations

from pathlib import Path

from api.feedback import MAX_ENTRIES, append_entry


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
