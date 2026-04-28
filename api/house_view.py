"""Rolling firm house-view memory.

Single-row markdown blob the EIC loads into every brief and rewrites after
every report. Turns a sequence of reports into a coherent narrative -- the
spec's central memory feature.

Storage: one row in `house_view` keyed by id=1. The markdown is structured
(headings: Rates, Equity, Dollar/FX, Top themes, Last updated) but the EIC
owns the prose. We don't parse it -- we round-trip it whole."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Session

from api.db import engine
from api.models import HouseView

INITIAL_VIEW = """# Forte House View

_No reports published yet. The first report will seed this view._

## Rates path
(unset)

## Equity stance
(unset)

## Dollar / FX
(unset)

## Top themes
(unset)
"""


def get() -> str:
    """Current rolling view. Returns the seed text on first read."""
    with Session(engine) as session:
        row = session.get(HouseView, 1)
        if row is None or not row.markdown.strip():
            return INITIAL_VIEW
        return row.markdown


def upsert(markdown: str, last_report_id: int | None = None) -> None:
    """Replace the rolling view in place. Idempotent."""
    with Session(engine) as session:
        row = session.get(HouseView, 1)
        if row is None:
            row = HouseView(id=1, markdown=markdown, last_report_id=last_report_id)
        else:
            row.markdown = markdown
            row.last_report_id = last_report_id
            row.updated_at = datetime.now(UTC)
        session.add(row)
        session.commit()


def last_updated() -> datetime | None:
    with Session(engine) as session:
        row = session.get(HouseView, 1)
        return row.updated_at if row else None
