"""Uploaded-document persistence + extraction tests."""

from __future__ import annotations

import pytest


@pytest.fixture
def env(monkeypatch, tmp_path):  # type: ignore[no-untyped-def]
    """Isolated DB + reports dir for upload tests."""
    from sqlmodel import SQLModel, create_engine
    eng = create_engine(f"sqlite:///{tmp_path}/u.db", connect_args={"check_same_thread": False})

    reports = tmp_path / "reports"
    reports.mkdir()

    import api.db as db_module
    import api.uploads as uploads_module
    from api import models  # noqa: F401

    monkeypatch.setattr(db_module, "engine", eng)
    monkeypatch.setattr(uploads_module, "engine", eng)
    import api.settings as settings_module
    monkeypatch.setattr(settings_module.settings, "reports_dir", reports)

    # Need a Report row so the foreign key holds.
    from api.models import Report
    SQLModel.metadata.create_all(eng)
    from sqlmodel import Session
    with Session(eng) as s:
        r = Report(theme="test theme")
        s.add(r); s.commit(); s.refresh(r)
        report_id = r.id

    yield eng, reports, report_id


def test_save_and_list(env) -> None:  # type: ignore[no-untyped-def]
    from api import uploads
    _, _, report_id = env
    doc = uploads.save_upload(report_id, "note.md", "text/markdown", b"# Heading\n\nBody copy.")
    assert doc.filename == "note.md"
    assert "Heading" in doc.extracted_text
    assert doc.size_bytes == len(b"# Heading\n\nBody copy.")
    docs = uploads.list_documents(report_id)
    assert len(docs) == 1
    assert docs[0].filename == "note.md"


def test_save_overwrites_same_name(env) -> None:  # type: ignore[no-untyped-def]
    from api import uploads
    _, _, report_id = env
    uploads.save_upload(report_id, "note.md", "text/markdown", b"first")
    uploads.save_upload(report_id, "note.md", "text/markdown", b"second-version-longer")
    docs = uploads.list_documents(report_id)
    assert len(docs) == 1
    assert "second-version" in docs[0].extracted_text


def test_csv_extraction(env) -> None:  # type: ignore[no-untyped-def]
    from api import uploads
    _, _, report_id = env
    csv = b"theme,score\nuranium,0.8\nGLP-1,0.95\n"
    doc = uploads.save_upload(report_id, "themes.csv", "text/csv", csv)
    assert "theme | score" in doc.extracted_text
    assert "uranium" in doc.extracted_text


def test_delete(env) -> None:  # type: ignore[no-untyped-def]
    from api import uploads
    _, _, report_id = env
    uploads.save_upload(report_id, "note.md", "text/markdown", b"x")
    assert uploads.delete_document(report_id, "note.md") is True
    assert uploads.list_documents(report_id) == []
    assert uploads.delete_document(report_id, "note.md") is False
