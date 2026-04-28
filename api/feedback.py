"""Feedback log management. The EIC's per-report notes append to each persona's
'## Feedback log' section. The persona file IS loaded into the agent's system
prompt at runtime, so recent feedback shapes the next assignment automatically.

Personas live in Postgres (api.personas); the filesystem is a write-through
cache rebuilt on startup via `hydrate_filesystem`. Feedback therefore has to go
through the DB or it gets wiped on the next Railway redeploy."""

from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path

log = logging.getLogger("feedback")

# Entries look like "### 2026-04-27 — report 42\n<body>". Date+id are the marker.
_ENTRY_RE = re.compile(r"^### \d{4}-\d{2}-\d{2}.*?$\n.*?(?=^### \d{4}-\d{2}-\d{2}|\Z)", re.S | re.M)
_LOG_HEADING = "## Feedback log"
_PLACEHOLDER_RE = re.compile(r"_No reports yet\._\s*", re.M)

MAX_ENTRIES = 10


def _apply_entry(text: str, *, body: str, report_id: int) -> str:
    """Pure markdown manipulation: append a dated entry under the feedback log,
    creating the section if missing, capping at MAX_ENTRIES (oldest evicted)."""
    today = date.today().isoformat()
    new_entry = f"### {today} — report {report_id}\n\n{body.strip()}\n\n"

    if _LOG_HEADING in text:
        head, _, log_body = text.partition(_LOG_HEADING)
        log_body = _PLACEHOLDER_RE.sub("", log_body).strip()
        existing = _ENTRY_RE.findall(log_body)
        kept = (existing + [new_entry])[-MAX_ENTRIES:]
        return (
            head.rstrip() + "\n\n"
            + _LOG_HEADING + "\n\n"
            + "".join(e if e.endswith("\n\n") else e.rstrip() + "\n\n" for e in kept)
        )
    return text.rstrip() + "\n\n" + _LOG_HEADING + "\n\n" + new_entry


def append_entry(persona_path: Path, *, body: str, report_id: int) -> None:
    """Filesystem-only appender. Kept for tests + ad-hoc tooling; production
    code should use `append_to_persona` so the DB row stays in sync."""
    text = persona_path.read_text(encoding="utf-8")
    persona_path.write_text(_apply_entry(text, body=body, report_id=report_id), encoding="utf-8")


def append_to_persona(slug: str, *, body: str, report_id: int) -> bool:
    """Append a feedback entry to a persona, persisting through the DB so the
    note survives Railway rebuilds. Writes both the DB row and the on-disk
    file (via `personas.upsert`). Returns False if the slug isn't known."""
    from api import personas

    text = personas.get_text(slug)
    if text is None:
        log.warning("feedback skipped: no persona for slug=%s", slug)
        return False
    new_text = _apply_entry(text, body=body, report_id=report_id)
    personas.upsert(slug, new_text)
    return True
