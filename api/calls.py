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

    def extract(
        self,
        *,
        prose: str,
        contributor_slugs: list[str],
        redteam_prose: str | None = None,
    ) -> AgentResult:
        slug_list = ", ".join(contributor_slugs) or "(none)"
        # Devil's advocate is a non-roster slug but earns a row when she
        # surfaces a "trade we're missing" the team hadn't put on. Include
        # her so the cover position table reflects redteam dissent rather
        # than only restating the team's own calls.
        redteam_blob = (
            "\n\nRED-TEAM NOTE (Saoirse Mok, devils-advocate slug `devils-advocate`). "
            "If she names a specific instrument under 'THE TRADE WE'RE MISSING' or "
            "calls a position in the body wrong instrument / wrong horizon, attribute "
            "her replacement call to `devils-advocate`. If she explicitly says a call "
            "the team made is the WRONG short or wrong instrument, DROP the team's "
            "version of that call from your output -- her note overrides:\n\n"
            f"{redteam_prose}\n"
            if redteam_prose and redteam_prose.strip() else ""
        )
        prompt = f"""You are a forecast extractor. Read the report below and return one row per directional call the analysts make. A "directional call" is a claim with all of:
- a tradable asset (stock ticker, ETF, FX pair, commodity, crypto -- something yfinance can price)
- a direction: long | short | fade | avoid
- a horizon (how long until the call should resolve)
- a contributing analyst (the section author)

Skip pure observations ("CPI is 3.2%"), pure framing ("the Fed is data-dependent"), and recommendations without a horizon. If you can't pin all four required fields confidently, drop the row -- under-recording is fine.

RECONCILIATION RULE (this is the failure mode we keep hitting): if the body's nearest verdict on the asset is a HOLD ("tactical hold", "neutral", "wait", "no position", "stand aside"), DROP the row. A "hold" is not a directional call. Likewise if the body says "downgrade to hold" or "no longer long", drop -- the cover position table cannot show a long on a name the body explicitly demotes.

PRICE TARGETS: when a directional call is on a SINGLE-NAME equity (an individual company ticker like NVDA, CCJ, BA -- not an ETF, index, FX pair, rate, commodity or crypto), the analyst is required to name a numeric price target. Capture it in `target_level` exactly as written ("to $185", "target $140", "PT 92"). If a single-stock call has no numeric target anywhere in the surrounding prose, DROP the row -- the firm's standard is that single-stock picks come with a number, and an extracted call without one is the failure mode this rule prevents. ETFs / indices / FX / rates / commodities / crypto are exempt -- leave `target_level` null for those.

CONTRIBUTOR SLUGS available (use exactly one of these as the contributor field, plus `devils-advocate` if a red-team note is provided): {slug_list}

REPORT:

{prose}{redteam_blob}

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


def calls_for_report(report_id: int) -> list[Call]:
    with Session(engine) as session:
        return list(session.exec(
            select(Call).where(Call.report_id == report_id)
            .order_by(Call.conviction.desc(), Call.id.asc())  # type: ignore[attr-defined]
        ).all())


def has_calls_for(report_id: int) -> bool:
    with Session(engine) as session:
        return session.exec(
            select(Call).where(Call.report_id == report_id).limit(1)
        ).first() is not None


def open_positions(limit: int = 100) -> list[Call]:
    """Calls that haven't matured yet (made_at + horizon_days > now and no
    evaluation written). The /positions page surfaces these as the firm's
    current stance."""
    with Session(engine) as session:
        rows = session.exec(
            select(Call).where(Call.evaluated_at.is_(None))  # type: ignore[union-attr]
            .order_by(Call.made_at.desc())  # type: ignore[attr-defined]
            .limit(limit)
        ).all()
    return list(rows)


def graded_history(limit: int = 200) -> list[Call]:
    """Resolved calls, most-recent first. Used by /positions page closed tab."""
    with Session(engine) as session:
        rows = session.exec(
            select(Call).where(Call.evaluated_at.is_not(None))  # type: ignore[union-attr]
            .order_by(Call.evaluated_at.desc())  # type: ignore[attr-defined]
            .limit(limit)
        ).all()
    return list(rows)


# ----- Basket / benchmark comparison -----

# Bearish directions get sign-flipped so a falling price reads as a winner.
_BEARISH = {CallDirection.short, CallDirection.fade, CallDirection.avoid}


def basket_vs_benchmark(
    *,
    side: str = "all",            # all | long | short
    min_conviction: int = 1,      # 1..5
    benchmark: str = "SPY",
) -> dict[str, Any]:
    """Compute a rolling equal-weighted basket return curve and a benchmark
    curve over the same window.

    Each day t after a position's `made_at`, the position contributes its
    daily price return (sign-flipped for bearish directions) until either
    `evaluated_at` (for closed) or today (for open). The basket's daily
    return is the mean of all live positions' daily returns; cumulative
    basket return is the compound product. Benchmark return is the simple
    cumulative price return of `benchmark` over the same window.

    Returns a JSON-friendly dict the dashboard plots."""
    import pandas as pd

    from api.data import yfinance as yf_data

    with Session(engine) as session:
        all_calls = list(session.exec(select(Call)).all())

    # Filter
    def _keep(c: Call) -> bool:
        if c.conviction < min_conviction:
            return False
        if side == "long" and c.direction != CallDirection.long:
            return False
        if side == "short" and c.direction not in _BEARISH:
            return False
        if c.price_at_call is None:
            return False
        return True

    calls = [c for c in all_calls if _keep(c)]
    if not calls:
        return {
            "dates": [], "basket": [], "benchmark": [],
            "n_positions": 0, "basket_return": None, "benchmark_return": None,
            "benchmark_ticker": benchmark, "side": side, "min_conviction": min_conviction,
        }

    now = datetime.now(UTC)
    earliest = min(
        (c.made_at if c.made_at.tzinfo else c.made_at.replace(tzinfo=UTC))
        for c in calls
    )
    age_days = (now - earliest).days
    if age_days <= 25:
        period = "3mo"
    elif age_days <= 170:
        period = "1y"
    elif age_days <= 350:
        period = "2y"
    else:
        period = "5y"

    # Fetch price history per unique asset + the benchmark (with fallbacks
    # for index tickers like ^GSPC that yfinance occasionally 404s on).
    bench_candidates = [benchmark]
    for fb in ("SPY", "^GSPC"):
        if fb not in bench_candidates:
            bench_candidates.append(fb)

    tickers = {c.asset for c in calls} | set(bench_candidates)
    closes: dict[str, pd.Series] = {}
    fetch_errors: dict[str, str] = {}
    for t in tickers:
        try:
            df = yf_data.get_history(t, period=period, interval="1d")
        except Exception as exc:  # noqa: BLE001
            log.exception("history fetch failed for %s", t)
            fetch_errors[t] = type(exc).__name__
            continue
        if df.empty or "Close" not in df.columns:
            fetch_errors.setdefault(t, "empty")
            continue
        s = df["Close"].dropna()
        if not isinstance(s.index, pd.DatetimeIndex):
            s.index = pd.to_datetime(s.index)
        s.index = s.index.tz_localize(None).normalize()
        closes[t] = s[~s.index.duplicated(keep="last")]

    bench_used = next((t for t in bench_candidates if t in closes), None)
    if bench_used is None:
        tried = ", ".join(f"{t} ({fetch_errors.get(t, 'missing')})" for t in bench_candidates)
        return {
            "dates": [], "basket": [], "benchmark": [],
            "n_positions": len(calls), "basket_return": None, "benchmark_return": None,
            "benchmark_ticker": benchmark, "side": side, "min_conviction": min_conviction,
            "error": f"benchmark unavailable from yfinance: tried {tried}",
        }
    benchmark = bench_used

    earliest_naive = pd.Timestamp(earliest).tz_convert(None).normalize() \
        if pd.Timestamp(earliest).tzinfo else pd.Timestamp(earliest).normalize()
    bench = closes[benchmark]
    bench = bench[bench.index >= earliest_naive]
    if bench.empty:
        return {
            "dates": [], "basket": [], "benchmark": [],
            "n_positions": len(calls), "basket_return": None, "benchmark_return": None,
            "benchmark_ticker": benchmark, "side": side, "min_conviction": min_conviction,
        }

    # Trading-day index = benchmark's index from earliest_naive.
    trading_days = bench.index

    # Per-asset daily returns aligned to trading_days.
    daily_returns: dict[str, pd.Series] = {}
    for t, s in closes.items():
        if t == benchmark:
            continue
        s_trim = s[s.index >= (earliest_naive - pd.Timedelta(days=5))]
        ret = s_trim.pct_change()
        daily_returns[t] = ret.reindex(trading_days)

    basket_daily = []
    for d in trading_days:
        contributions: list[float] = []
        for c in calls:
            made = c.made_at if c.made_at.tzinfo else c.made_at.replace(tzinfo=UTC)
            made_naive = pd.Timestamp(made).tz_convert(None).normalize() \
                if pd.Timestamp(made).tzinfo else pd.Timestamp(made).normalize()
            if d <= made_naive:
                continue
            if c.evaluated_at is not None:
                ev = c.evaluated_at if c.evaluated_at.tzinfo else c.evaluated_at.replace(tzinfo=UTC)
                ev_naive = pd.Timestamp(ev).tz_convert(None).normalize() \
                    if pd.Timestamp(ev).tzinfo else pd.Timestamp(ev).normalize()
                if d > ev_naive:
                    continue
            r = daily_returns.get(c.asset)
            if r is None or d not in r.index:
                continue
            v = r.loc[d]
            if v is None or pd.isna(v):
                continue
            v = float(v)
            if c.direction in _BEARISH:
                v = -v
            contributions.append(v)
        basket_daily.append(sum(contributions) / len(contributions) if contributions else 0.0)

    # Compound to cumulative.
    basket_cum: list[float] = []
    acc = 1.0
    for r in basket_daily:
        acc *= (1.0 + r)
        basket_cum.append(acc - 1.0)

    bench_first = float(bench.iloc[0])
    bench_cum = [(float(v) / bench_first - 1.0) if bench_first else 0.0 for v in bench]

    return {
        "dates": [d.strftime("%Y-%m-%d") for d in trading_days],
        "basket": basket_cum,
        "benchmark": bench_cum,
        "n_positions": len(calls),
        "basket_return": basket_cum[-1] if basket_cum else None,
        "benchmark_return": bench_cum[-1] if bench_cum else None,
        "benchmark_ticker": benchmark,
        "side": side,
        "min_conviction": min_conviction,
    }
