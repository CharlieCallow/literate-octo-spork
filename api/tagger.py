"""Theme + ticker extractor.

Reads the audited prose and pulls out the named tickers and themes the report
covered. Stored on the Report row (`mentioned_tickers`, `mentioned_themes`)
and read by the Scout to weight natural follow-ups in the daily digest.

We use Haiku because this is mechanical pattern-spotting, not reasoning."""

from __future__ import annotations

import json
import logging
import re

from sqlmodel import Session

from api.agents.base import Agent, AgentResult
from api.agents.cost import CostTracker
from api.db import engine
from api.models import Report
from api.settings import settings

log = logging.getLogger("tagger")


class Tagger(Agent):
    role = "tagger"
    default_model = settings.model_haiku

    def __init__(self, cost: CostTracker, **kwargs: object) -> None:
        super().__init__(
            persona_path=settings.team_dir / "editor-in-chief.md",
            cost=cost,
            **kwargs,  # type: ignore[arg-type]
        )

    def tag(self, *, prose: str) -> AgentResult:
        prompt = f"""Extract the tickers and themes this research report covers. Strict JSON output, nothing else.

REPORT:

{prose}

Rules:
- Tickers: yfinance-compatible symbols mentioned in the prose. Stocks, ETFs, FX pairs (EURUSD=X), commodities (CL=F), crypto (BTC-USD), indices (^VIX). Skip tickers mentioned in passing comparisons; only the ones the report has a take on.
- Themes: short noun phrases (2-4 words) describing what the report is actually about. Examples: "AI capex", "nuclear renaissance", "GLP-1 second-order effects", "memory bandwidth", "OPEC discipline". 3-7 themes per report. Generic ones like "macro", "equities", "crypto" don't count.

Output format:

{{"tickers": ["SPY", "CCJ"], "themes": ["nuclear renaissance", "uranium fuel cycle"]}}

If nothing qualifies, return: {{"tickers": [], "themes": []}}
"""
        return self.run(prompt, max_tokens=1024, max_iters=1)


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


def parse(text: str) -> tuple[list[str], list[str]]:
    """Parse the tagger's JSON. Returns (tickers, themes)."""
    s = text.strip()
    if m := _JSON_FENCE_RE.search(s):
        s = m.group(1)
    try:
        data = json.loads(s)
    except json.JSONDecodeError:
        return [], []
    if not isinstance(data, dict):
        return [], []

    def _clean(items: object, *, upper: bool, max_n: int) -> list[str]:
        if not isinstance(items, list):
            return []
        out: list[str] = []
        seen: set[str] = set()
        for x in items:
            if not isinstance(x, str):
                continue
            v = x.strip()
            if upper:
                v = v.upper().replace(" ", "")
            if not v:
                continue
            # Dedupe case-insensitively (themes especially -- "AI capex" and
            # "ai capex" are the same tag) but preserve the first-seen casing.
            key = v.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(v)
            if len(out) >= max_n:
                break
        return out

    return _clean(data.get("tickers"), upper=True, max_n=20), _clean(data.get("themes"), upper=False, max_n=10)


def persist(report_id: int, tickers: list[str], themes: list[str]) -> None:
    with Session(engine) as session:
        r = session.get(Report, report_id)
        if not r:
            return
        r.mentioned_tickers = tickers
        r.mentioned_themes = themes
        session.add(r)
        session.commit()


def recent_themes(limit_reports: int = 8) -> list[str]:
    """Tags from the most recent N done reports, deduped, most-recent-first.

    Read by the Scout's daily digest so it weights natural follow-ups higher
    than cold themes."""
    from sqlmodel import select

    from api.models import ReportStage

    with Session(engine) as session:
        rows = session.exec(
            select(Report)
            .where(Report.stage == ReportStage.done)
            .order_by(Report.created_at.desc())  # type: ignore[attr-defined]
            .limit(limit_reports)
        ).all()
    out: list[str] = []
    seen: set[str] = set()
    for r in rows:
        for t in r.mentioned_themes or []:
            key = t.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(t)
    return out
