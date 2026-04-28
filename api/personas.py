"""Persona persistence helpers.

Postgres is the source of truth. The team/ filesystem is a write-through
cache: every change to a row also writes the file, and `hydrate_filesystem`
rebuilds the file tree from DB rows at startup so Railway / Vercel rebuilds
don't lose promotions, firings, or manual edits.

Layout:
- standing personas -> team/<slug>.md
- temp personas     -> team/temp/<slug>.md
- archived personas -> team/archive/<slug>.md
- orchestrator personas (editor-in-chief, scout, recruiter, data-and-charts)
  always live in team/<slug>.md regardless of status.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from pathlib import Path

from sqlmodel import Session, select

from api.db import engine
from api.models import Persona, PersonaStatus
from api.settings import settings

log = logging.getLogger("personas")

ORCHESTRATOR_SLUGS = {"editor-in-chief", "data-and-charts", "scout", "recruiter"}
_HEADER_RE = re.compile(r"^#\s+(?P<name>.+?)\s+(?:—|-+)\s+(?P<role>.+)$", re.M)


def _parse_header(text: str) -> tuple[str, str]:
    """Best-effort name/role extraction from a persona's `# Name -- Role` line."""
    if m := _HEADER_RE.search(text):
        return m.group("name").strip(), m.group("role").strip()
    return "", ""


def _path_for(p: Persona) -> Path:
    """Where this persona's file should live on disk based on status."""
    if p.is_orchestrator:
        return settings.team_dir / f"{p.slug}.md"
    if p.status == PersonaStatus.temp:
        return settings.team_dir / "temp" / f"{p.slug}.md"
    if p.status == PersonaStatus.archived:
        return settings.team_dir / "archive" / f"{p.slug}.md"
    return settings.team_dir / f"{p.slug}.md"


def seed_from_filesystem() -> int:
    """One-shot: if the personas table is empty, seed it from team/*.md.
    Returns the number of rows inserted."""
    with Session(engine) as session:
        existing = session.exec(select(Persona)).first()
        if existing is not None:
            return 0

        added = 0
        # Standing roster + orchestrators (everything directly in team/)
        for path in sorted(settings.team_dir.glob("*.md")):
            slug = path.stem
            text = path.read_text(encoding="utf-8")
            name, role = _parse_header(text)
            session.add(Persona(
                slug=slug,
                status=PersonaStatus.standing,
                markdown=text,
                name=name,
                role=role,
                is_orchestrator=slug in ORCHESTRATOR_SLUGS,
            ))
            added += 1

        # Temp specialists (any markdown files in team/temp/)
        temp_dir = settings.team_dir / "temp"
        if temp_dir.exists():
            for path in sorted(temp_dir.glob("*.md")):
                slug = path.stem
                text = path.read_text(encoding="utf-8")
                name, role = _parse_header(text)
                session.add(Persona(
                    slug=slug,
                    status=PersonaStatus.temp,
                    markdown=text,
                    name=name,
                    role=role,
                ))
                added += 1

        # Archived personas
        archive_dir = settings.team_dir / "archive"
        if archive_dir.exists():
            for path in sorted(archive_dir.glob("*.md")):
                slug = path.stem
                text = path.read_text(encoding="utf-8")
                name, role = _parse_header(text)
                session.add(Persona(
                    slug=slug,
                    status=PersonaStatus.archived,
                    markdown=text,
                    name=name,
                    role=role,
                ))
                added += 1

        session.commit()
        log.info("seeded %d personas from filesystem", added)
        return added


def hydrate_filesystem() -> None:
    """Write every Persona row to its expected path on disk. Called at startup
    to restore team/, team/temp/, team/archive/ from DB after a rebuild."""
    settings.team_dir.mkdir(parents=True, exist_ok=True)
    (settings.team_dir / "temp").mkdir(exist_ok=True)
    (settings.team_dir / "archive").mkdir(exist_ok=True)

    with Session(engine) as session:
        rows = session.exec(select(Persona)).all()
    for p in rows:
        path = _path_for(p)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(p.markdown, encoding="utf-8")
    log.info("hydrated %d persona files to disk", len(rows))


def upsert(slug: str, markdown: str, *, status: PersonaStatus | None = None) -> Persona:
    """Insert or update a persona. Writes the file as well."""
    name, role = _parse_header(markdown)
    is_orch = slug in ORCHESTRATOR_SLUGS
    with Session(engine) as session:
        p = session.get(Persona, slug)
        if p is None:
            p = Persona(
                slug=slug,
                status=status or PersonaStatus.standing,
                markdown=markdown,
                name=name,
                role=role,
                is_orchestrator=is_orch,
            )
        else:
            p.markdown = markdown
            p.name = name or p.name
            p.role = role or p.role
            if status is not None:
                p.status = status
            p.updated_at = datetime.now(UTC)
        session.add(p)
        session.commit()
        session.refresh(p)
        path = _path_for(p)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown, encoding="utf-8")
        return p


def set_status(slug: str, status: PersonaStatus) -> Persona | None:
    """Update status; move the file to its new home."""
    with Session(engine) as session:
        p = session.get(Persona, slug)
        if p is None:
            return None
        old_path = _path_for(p)
        p.status = status
        p.updated_at = datetime.now(UTC)
        new_path = _path_for(p)
        session.add(p)
        session.commit()
        session.refresh(p)
        # Move the file if the path changed.
        if old_path != new_path:
            new_path.parent.mkdir(parents=True, exist_ok=True)
            if old_path.exists():
                old_path.rename(new_path)
            else:
                new_path.write_text(p.markdown, encoding="utf-8")
        return p


def get_text(slug: str) -> str | None:
    """Return a persona's markdown by slug, looking in DB first, FS as fallback."""
    with Session(engine) as session:
        p = session.get(Persona, slug)
        if p is not None:
            return p.markdown
    # Fallback: maybe seeding hasn't happened yet (e.g. a fresh test fixture).
    for sub in ("", "temp", "archive"):
        path = settings.team_dir / sub / f"{slug}.md" if sub else settings.team_dir / f"{slug}.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
    return None


def list_by_status(status: PersonaStatus) -> list[Persona]:
    with Session(engine) as session:
        return list(session.exec(
            select(Persona).where(Persona.status == status).order_by(Persona.slug)  # type: ignore[arg-type]
        ).all())


def get(slug: str) -> Persona | None:
    with Session(engine) as session:
        return session.get(Persona, slug)
