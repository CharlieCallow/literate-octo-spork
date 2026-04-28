"""Persona persistence tests: seed from filesystem, hydrate back, status moves."""

from __future__ import annotations

import pytest


@pytest.fixture
def env(monkeypatch, tmp_path):  # type: ignore[no-untyped-def]
    """Isolated DB + team directory with a couple seed personas."""
    from sqlmodel import SQLModel, create_engine
    eng = create_engine(f"sqlite:///{tmp_path}/p.db", connect_args={"check_same_thread": False})

    standing = tmp_path / "team"
    temp = standing / "temp"
    archive = standing / "archive"
    standing.mkdir()
    temp.mkdir()
    archive.mkdir()
    (standing / "macro-strategist.md").write_text(
        "# Henrik Voss — Macro Strategist\n\nBio.", encoding="utf-8",
    )
    (standing / "editor-in-chief.md").write_text(
        "# Margaux Devlin — Editor-in-Chief\n\nBio.", encoding="utf-8",
    )

    import api.db as db_module
    import api.personas as personas_module
    from api import models  # noqa: F401

    monkeypatch.setattr(db_module, "engine", eng)
    monkeypatch.setattr(personas_module, "engine", eng)
    import api.settings as settings_module
    monkeypatch.setattr(type(settings_module.settings), "team_dir", property(lambda self: standing))

    SQLModel.metadata.create_all(eng)
    yield eng, standing, temp, archive


def test_seed_from_filesystem_idempotent(env) -> None:  # type: ignore[no-untyped-def]
    from api.personas import seed_from_filesystem
    n1 = seed_from_filesystem()
    n2 = seed_from_filesystem()
    assert n1 == 2
    assert n2 == 0  # already seeded; no-op


def test_seed_marks_orchestrators(env) -> None:  # type: ignore[no-untyped-def]
    from api.personas import get, seed_from_filesystem
    seed_from_filesystem()
    eic = get("editor-in-chief")
    macro = get("macro-strategist")
    assert eic is not None
    assert macro is not None
    assert eic.is_orchestrator is True
    assert macro.is_orchestrator is False


def test_set_status_moves_file(env) -> None:  # type: ignore[no-untyped-def]
    _, standing, _temp, archive = env
    from api.models import PersonaStatus
    from api.personas import seed_from_filesystem, set_status
    seed_from_filesystem()
    set_status("macro-strategist", PersonaStatus.archived)
    assert not (standing / "macro-strategist.md").exists()
    assert (archive / "macro-strategist.md").exists()


def test_hydrate_filesystem_restores_files(env) -> None:  # type: ignore[no-untyped-def]
    _, standing, _temp, _archive = env
    from api.personas import hydrate_filesystem, seed_from_filesystem
    seed_from_filesystem()
    # Wipe the FS as if Railway just rebuilt
    for p in standing.glob("*.md"):
        p.unlink()
    assert not (standing / "macro-strategist.md").exists()
    hydrate_filesystem()
    assert (standing / "macro-strategist.md").exists()
    assert (standing / "editor-in-chief.md").exists()


def test_upsert_creates_then_updates(env) -> None:  # type: ignore[no-untyped-def]
    from api.personas import get, upsert
    upsert("new-analyst", "# New Analyst — Specialist\n\nBio v1.")
    assert get("new-analyst").markdown.startswith("# New Analyst")  # type: ignore[union-attr]
    upsert("new-analyst", "# New Analyst — Specialist\n\nBio v2.")
    assert "v2" in get("new-analyst").markdown  # type: ignore[union-attr]
