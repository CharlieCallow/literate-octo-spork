"""Feedback log management. The EIC's per-report notes append to each persona's
'## Feedback log' section. The persona file IS loaded into the agent's system
prompt at runtime, so recent feedback shapes the next assignment automatically."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

# Entries look like "### 2026-04-27 — report 42\n<body>". Date+id are the marker.
_ENTRY_RE = re.compile(r"^### \d{4}-\d{2}-\d{2}.*?$\n.*?(?=^### \d{4}-\d{2}-\d{2}|\Z)", re.S | re.M)
_LOG_HEADING = "## Feedback log"
_PLACEHOLDER_RE = re.compile(r"_No reports yet\._\s*", re.M)

MAX_ENTRIES = 10


def append_entry(persona_path: Path, *, body: str, report_id: int) -> None:
    """Append a dated feedback entry. Caps the log at MAX_ENTRIES (oldest evicted)."""
    today = date.today().isoformat()
    new_entry = f"### {today} — report {report_id}\n\n{body.strip()}\n\n"

    text = persona_path.read_text(encoding="utf-8")

    if _LOG_HEADING in text:
        head, _, log_body = text.partition(_LOG_HEADING)
        log_body = _PLACEHOLDER_RE.sub("", log_body).strip()
        existing = _ENTRY_RE.findall(log_body)
        # Append the new entry; keep only the most recent MAX_ENTRIES.
        kept = (existing + [new_entry])[-MAX_ENTRIES:]
        new_text = (
            head.rstrip() + "\n\n"
            + _LOG_HEADING + "\n\n"
            + "".join(e if e.endswith("\n\n") else e.rstrip() + "\n\n" for e in kept)
        )
    else:
        new_text = text.rstrip() + "\n\n" + _LOG_HEADING + "\n\n" + new_entry

    persona_path.write_text(new_text, encoding="utf-8")
