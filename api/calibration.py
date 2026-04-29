"""Per-analyst conviction calibration.

The `calls` table tracks every directional forecast extracted from a
published report and grades it once the horizon matures. This module
turns those rows into a calibration signal the firm can actually use:

- Brief stage reads it so the EIC weights contributions ("Henrik's last
  8 calls: 0.62 hit rate; trust him on rates more than equities").
- Feedback stage appends it to the persona's feedback log so the
  analyst sees their own track record over time.
- Team page renders it as a per-row stat so you can spot drift.

Tied to the `is_test` filter on Report -- test-mode runs don't extract
calls, so they're naturally excluded, but we double-check via the
join in case a future feature drift records a call against a test row."""

from __future__ import annotations

from dataclasses import dataclass

from sqlmodel import Session, select

from api.db import engine
from api.models import Call, CallOutcome, Report

# Minimum graded-call count before we treat a calibration number as
# meaningful. Below this threshold the brief / team page should still
# show n but suppress the percentage -- 1-2 calls aren't a track record.
_MIN_GRADED_FOR_RATE = 3


@dataclass
class Calibration:
    slug: str
    n_total: int          # all calls ever made (graded or not)
    n_graded: int         # calls past their horizon, scored
    n_hit: int
    n_partial: int
    n_miss: int
    # hit_rate counts a partial as 0.5 of a hit. None when n_graded
    # is below the threshold -- the UI uses this to show "n=2 — early"
    # rather than a misleading 50%.
    hit_rate: float | None
    # Top conviction tier the analyst tagged on average (1-5). Higher
    # means they're claiming more confidence; combined with hit_rate
    # this exposes calibration drift (high conviction, low hit rate).
    avg_conviction: float | None


def _empty(slug: str) -> Calibration:
    return Calibration(
        slug=slug, n_total=0, n_graded=0,
        n_hit=0, n_partial=0, n_miss=0,
        hit_rate=None, avg_conviction=None,
    )


def for_persona(slug: str) -> Calibration:
    """Single-persona calibration. O(1) DB hit; cheap enough to call
    inline from the brief stage without batching."""
    with Session(engine) as session:
        rows = list(session.exec(
            select(Call)
            .join(Report, Report.id == Call.report_id)  # type: ignore[arg-type]
            .where(Call.contributor_slug == slug)
            .where(Report.is_test == False)  # noqa: E712
        ).all())
    return _from_rows(slug, rows)


def for_all() -> dict[str, Calibration]:
    """Calibration for every analyst with at least one call. Used by the
    team page; one query, group in Python."""
    with Session(engine) as session:
        rows = list(session.exec(
            select(Call)
            .join(Report, Report.id == Call.report_id)  # type: ignore[arg-type]
            .where(Report.is_test == False)  # noqa: E712
        ).all())
    by_slug: dict[str, list[Call]] = {}
    for r in rows:
        by_slug.setdefault(r.contributor_slug, []).append(r)
    return {slug: _from_rows(slug, calls) for slug, calls in by_slug.items()}


def _from_rows(slug: str, rows: list[Call]) -> Calibration:
    if not rows:
        return _empty(slug)
    graded = [r for r in rows if r.outcome is not None]
    n_hit = sum(1 for r in graded if r.outcome == CallOutcome.hit)
    n_partial = sum(1 for r in graded if r.outcome == CallOutcome.partial)
    n_miss = sum(1 for r in graded if r.outcome == CallOutcome.miss)
    hit_rate: float | None = None
    if len(graded) >= _MIN_GRADED_FOR_RATE:
        hit_rate = (n_hit + 0.5 * n_partial) / len(graded)
    convictions = [r.conviction for r in rows if r.conviction]
    avg_conviction = (sum(convictions) / len(convictions)) if convictions else None
    return Calibration(
        slug=slug,
        n_total=len(rows),
        n_graded=len(graded),
        n_hit=n_hit,
        n_partial=n_partial,
        n_miss=n_miss,
        hit_rate=hit_rate,
        avg_conviction=avg_conviction,
    )


def format_for_brief(calibration: Calibration, name: str | None = None) -> str:
    """One-line summary for the EIC's brief prompt. Returns "" when the
    analyst has no graded calls -- a brand-new persona shouldn't be
    weighted by a non-existent track record."""
    if calibration.n_graded == 0:
        return ""
    label = name or calibration.slug
    if calibration.hit_rate is None:
        return f"{label}: {calibration.n_graded} graded calls (early -- no rate)"
    pct = int(round(calibration.hit_rate * 100))
    conv = (
        f", avg conviction {calibration.avg_conviction:.1f}/5"
        if calibration.avg_conviction else ""
    )
    return f"{label}: {pct}% hit rate over {calibration.n_graded} graded calls{conv}"


def format_for_feedback(calibration: Calibration) -> str:
    """Multi-line block for the persona's feedback-log appendix. Written
    after every report the analyst contributed to so their persona file
    accumulates a calibration timeline."""
    if calibration.n_total == 0:
        return ""
    lines = [
        f"Calls so far: {calibration.n_total} total, {calibration.n_graded} graded.",
    ]
    if calibration.n_graded > 0:
        lines.append(
            f"Outcomes: {calibration.n_hit} hit, "
            f"{calibration.n_partial} partial, {calibration.n_miss} miss."
        )
    if calibration.hit_rate is not None:
        lines.append(f"Hit rate (partial = 0.5): {calibration.hit_rate:.2f}")
    if calibration.avg_conviction is not None:
        lines.append(f"Avg conviction tag: {calibration.avg_conviction:.2f}/5")
    if (
        calibration.hit_rate is not None
        and calibration.avg_conviction is not None
        and calibration.avg_conviction >= 4.0
        and calibration.hit_rate < 0.5
    ):
        lines.append(
            "Calibration drift: high conviction, low hit rate. Tighten "
            "your c4/c5 tags to claims you'd defend with money."
        )
    return "\n".join(lines)
