"""Performance ledger.

Every published report writes a structured `Call` row per directional claim
(asset, direction, horizon, conviction, target). A weekly cron walks the
unresolved calls past their horizon, fetches current prices via yfinance, and
grades them. Hit rate per persona then feeds the team page and the recruiter's
fire logic.

Extraction: Haiku-tier pass over the audited prose. Conservative -- if the
model can't pin down all four required fields (asset, direction, horizon,
contributor) the call is dropped. Better to under-record than fabricate."""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlmodel import Session, select

from api.agents.base import Agent, AgentResult
from api.agents.cost import CostTracker
from api.db import engine
from api.models import Call, CallDirection, CallOutcome
from api.settings import settings

log = logging.getLogger("calls")


_VALID_DIRECTIONS = {d.value for d in CallDirection}


class CallExtractor(Agent):
    """Haiku-tier reader. Pulls structured forecasts from the audited prose."""

    role = "call-extractor"
    default_model = settings.model_haiku

    def __init__(self, cost: CostTracker, **kwargs: object) -> None:
        super().__init__(
            persona_path=settings.team_dir / "editor-in-chief.md",
            cost=cost,
            **kwargs,  # type: ignore[arg-type]
        )

    def extract(self, *, prose: str, contributor_slugs: list[str]) -> AgentResult:
        slug_list = ", ".join(contributor_slugs) or "(none)"
        prompt = f"""You are a forecast extractor. Read the report below and return one row per directional call the analysts make. A "directional call" is a claim with all of:
- a tradable asset (stock ticker, ETF, FX pair, commodity, crypto -- something yfinance can price)
- a direction: long | short | fade | avoid
- a horizon (how long until the call should resolve)
- a contributing analyst (the section author)

Skip pure observations ("CPI is 3.2%"), pure framing ("the Fed is data-dependent"), and recommendations without a horizon. If you can't pin all four required fields confidently, drop the row -- under-recording is fine.

CONTRIBUTOR SLUGS available (use exactly one of these as the contributor field): {slug_list}

REPORT:

{prose}

Return STRICT JSON in this shape and nothing else (no prose, no code fence):

{{"calls": [
  {{
    "asset": "<yfinance ticker, e.g. SPY, CCJ, BTC-USD, ^VIX, CL=F>",
    "direction": "long" | "short" | "fade" | "avoid",
    "horizon_days": <integer days, default 90 if a horizon is implied but not specific>,
    "target_level": <float or null -- only if a price/yield target is named>,
    "conviction": <integer 1-5; default 3 if no tag>,
    "contributor_slug": "<one of the slugs above>",
    "claim": "<short prose snippet, ~12 words, that backs the call>"
  }}
]}}

If the report makes no qualifying calls, return: {{"calls": []}}
"""
        return self.run(prompt, max_tokens=2048, max_iters=1)


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


def _strip_fence(s: str) -> str:
    if m := _JSON_FENCE_RE.search(s):
        return m.group(1)
    return s


def parse_extracted(text: str) -> list[dict[str, Any]]:
    """Parse the JSON returned by CallExtractor. Tolerant of code fences."""
    s = _strip_fence(text.strip())
    try:
        data = json.loads(s)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    raw = data.get("calls", [])
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for c in raw:
        if not isinstance(c, dict):
            continue
        # Strict-ish field check; conservative -- drop on missing required.
        if not (asset := c.get("asset")):
            continue
        direction = str(c.get("direction", "")).lower()
        if direction not in _VALID_DIRECTIONS:
            continue
        if not (slug := c.get("contributor_slug")):
            continue
        try:
            horizon = int(c.get("horizon_days") or 90)
            conviction = int(c.get("conviction") or 3)
        except (TypeError, ValueError):
            continue
        target = c.get("target_level")
        try:
            target_f = float(target) if target is not None else None
        except (TypeError, ValueError):
            target_f = None
        out.append({
            "asset": str(asset).strip().upper().replace(" ", ""),
            "direction": direction,
            "horizon_days": max(1, horizon),
            "conviction": max(1, min(5, conviction)),
            "target_level": target_f,
            "contributor_slug": str(slug).strip(),
            "claim_text": str(c.get("claim", ""))[:500],
        })
    return out


def persist(report_id: int, parsed: list[dict[str, Any]]) -> int:
    """Insert one Call row per parsed entry. Returns count inserted."""
    if not parsed:
        return 0
    inserted = 0
    with Session(engine) as session:
        for c in parsed:
            try:
                price_at_call = _spot_price(c["asset"])
            except Exception:  # noqa: BLE001
                price_at_call = None
            session.add(Call(
                report_id=report_id,
                contributor_slug=c["contributor_slug"],
                asset=c["asset"],
                direction=CallDirection(c["direction"]),
                horizon_days=c["horizon_days"],
                target_level=c["target_level"],
                conviction=c["conviction"],
                claim_text=c["claim_text"],
                price_at_call=price_at_call,
            ))
            inserted += 1
        session.commit()
    return inserted


def _spot_price(ticker: str) -> float | None:
    """Fetch the most recent close. Returns None on any failure."""
    from api.data import yfinance as yf_data
    df = yf_data.get_history(ticker, period="5d", interval="1d")
    if df.empty or "Close" not in df.columns:
        return None
    closes = df["Close"].dropna()
    return float(closes.iloc[-1]) if len(closes) else None


def evaluate_due() -> int:
    """Walk every unresolved Call past its horizon and grade it.

    Hit/miss logic:
    - long  : hit if price has risen vs price_at_call (or hit target_level)
    - short : hit if price has fallen
    - fade  : same as short -- the call is to bet against the move
    - avoid : hit if price is flat-to-down (we recommended NOT owning it)
    Partial: if a target_level was named and we got >50% of the way there
    without crossing it, mark partial.
    Returns the number of calls graded."""
    now = datetime.now(UTC)
    graded = 0
    with Session(engine) as session:
        rows = session.exec(
            select(Call).where(Call.evaluated_at.is_(None))  # type: ignore[union-attr]
        ).all()
        for c in rows:
            made_at = c.made_at if c.made_at.tzinfo else c.made_at.replace(tzinfo=UTC)
            if now - made_at < timedelta(days=c.horizon_days):
                continue
            try:
                spot = _spot_price(c.asset)
            except Exception:  # noqa: BLE001
                log.exception("price fetch failed for %s", c.asset)
                continue
            if spot is None or c.price_at_call is None:
                continue
            outcome = _grade_one(
                direction=c.direction, target=c.target_level,
                start=c.price_at_call, end=spot,
            )
            c.price_at_evaluation = spot
            c.evaluated_at = now
            c.outcome = outcome
            session.add(c)
            graded += 1
        session.commit()
    log.info("evaluated %d due calls", graded)
    return graded


def _grade_one(*, direction: CallDirection, target: float | None,
               start: float, end: float) -> CallOutcome:
    """Rule-based grade. Conservative -- prefers `partial` over `hit` when
    the move is in the right direction but didn't reach a named target."""
    move_pct = (end - start) / start if start else 0.0
    direction_right = (
        (direction == CallDirection.long and move_pct > 0)
        or (direction in (CallDirection.short, CallDirection.fade) and move_pct < 0)
        or (direction == CallDirection.avoid and move_pct <= 0)
    )

    if target is not None and start:
        target_move = (target - start) / start
        # Did the move cross the target?
        crossed = (
            (direction == CallDirection.long and end >= target)
            or (direction in (CallDirection.short, CallDirection.fade) and end <= target)
        )
        if crossed:
            return CallOutcome.hit
        # Right direction, partway to target.
        if direction_right and target_move and abs(move_pct / target_move) >= 0.5:
            return CallOutcome.partial
        return CallOutcome.miss

    # No target -- direction alone decides.
    return CallOutcome.hit if direction_right else CallOutcome.miss


def hit_rate_by_persona() -> dict[str, dict[str, float | int]]:
    """Per-slug aggregate: graded count + hit-rate (hit + 0.5*partial)."""
    with Session(engine) as session:
        rows = session.exec(
            select(Call).where(Call.outcome.is_not(None))  # type: ignore[union-attr]
        ).all()
    out: dict[str, dict[str, float | int]] = {}
    for r in rows:
        bucket = out.setdefault(r.contributor_slug, {"graded": 0, "score": 0.0})
        bucket["graded"] = int(bucket["graded"]) + 1
        if r.outcome == CallOutcome.hit:
            bucket["score"] = float(bucket["score"]) + 1.0
        elif r.outcome == CallOutcome.partial:
            bucket["score"] = float(bucket["score"]) + 0.5
    for slug, agg in out.items():
        graded = int(agg["graded"]) or 1
        agg["hit_rate"] = float(agg["score"]) / graded
    return out


def last_evaluation_at() -> datetime | None:
    """Most-recent evaluated_at across all calls. Used by the worker to gate
    weekly runs."""
    with Session(engine) as session:
        rows = session.exec(
            select(Call).where(Call.evaluated_at.is_not(None))  # type: ignore[union-attr]
        ).all()
    times = [r.evaluated_at for r in rows if r.evaluated_at]
    return max(times) if times else None
