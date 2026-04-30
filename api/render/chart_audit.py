"""Chart-vs-title sanity check.

The Data & Charts agent sometimes produces a PNG whose title makes a
specific comparative claim ("PPA $/MWh by Deal", "Restart vs. Greenfield")
but whose underlying data is something else entirely -- a stock-price line
mislabelled as a categorical bar chart, a single point advertised as a
range, etc. The audit reads each chart's `.json` sidecar (written by
`api/render/charts.py`) and flags mismatches between title intent and
data shape. Failing charts get their PNG renamed so the workflow's
chart-substitution path in `_inline_charts` picks a different chart for
that section's reference, and the prose never points at a broken visual.

Heuristics, not LLM. The mismatches we've actually seen are coarse:
- title says "by <X>" / "across <X>" -> categorical bar with >= 2 distinct
  string-shaped categories. A bar chart whose index is dates, or whose
  series has 50+ entries, isn't "by Deal".
- title says "<A> vs <B>" or "compared to" -> at least 2 series. A single
  line is not a comparison.
- chart "kind" of `bar` with date-shaped index is almost always a
  mislabelled time series.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from pathlib import Path

log = logging.getLogger(__name__)


# Title phrases that imply a categorical comparison across named buckets.
# The chart should be a bar with a small set of string categories on the
# index axis. A line chart, or a bar with date-stamped index, fails.
_CATEGORICAL_TITLE_RE = re.compile(
    r"\b(?:by\s+(?:deal|name|ticker|reactor|company|segment|asset|region|"
    r"vintage|type|class)|across\s+\w+|breakdown|comparison\s+by)\b",
    re.I,
)
# Title phrases that imply >= 2 series on a shared axis (line/comparison).
_MULTI_SERIES_TITLE_RE = re.compile(
    r"\b(?:vs\.?|versus|compared\s+to|relative\s+to|premium\s+(?:to|over))\b",
    re.I,
)
# Detect ISO-ish date strings in a sample of index entries.
_DATE_LIKE_RE = re.compile(r"^\d{4}-\d{2}(?:-\d{2})?(?:[T ]\d|$)")


def _is_date_like(values: list[str]) -> bool:
    if not values:
        return False
    sample = values[:10]
    return sum(1 for v in sample if _DATE_LIKE_RE.match(v)) >= max(1, len(sample) // 2)


def _series_count(payload: dict) -> int:
    series = payload.get("series") or {}
    if isinstance(series, dict):
        return len(series)
    return 0


def _index_values(payload: dict) -> list[str]:
    idx = payload.get("index") or []
    if not isinstance(idx, list):
        return []
    return [str(v) for v in idx]


def audit_one(sidecar_path: Path) -> tuple[bool, str | None]:
    """Return (ok, reason). `reason` is set only when ok=False."""
    try:
        payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return True, None  # no sidecar -> can't audit, don't penalise
        _ = e
    title = str(payload.get("title") or "")
    subtitle = str(payload.get("subtitle") or "")
    full_title = f"{title} {subtitle}"
    kind = str(payload.get("kind") or "")
    index = _index_values(payload)
    n_series = _series_count(payload)
    n_index = len(index)

    if _CATEGORICAL_TITLE_RE.search(full_title):
        # Title implies named buckets. We expect a bar with a handful of
        # string categories. A 100-entry index, or a date-shaped one, is
        # the failure mode the reviewer flagged.
        if kind != "bar":
            return False, f"title implies categorical comparison but chart kind is '{kind}'"
        if _is_date_like(index):
            return False, "title implies categorical comparison but index is date-like"
        if n_index > 25:
            return False, f"title implies categorical comparison but index has {n_index} entries"
        if n_index < 2:
            return False, "title implies categorical comparison but fewer than 2 categories"

    if _MULTI_SERIES_TITLE_RE.search(full_title):
        # "X vs Y" needs at least two series to compare. A single line
        # advertised as a comparison is the second reviewer-flagged mode.
        if n_series < 2:
            return False, f"title implies multi-series comparison but only {n_series} series present"

    return True, None


def audit_charts_dir(charts_dir: Path) -> list[tuple[Path, str]]:
    """Walk every `*.png` in `charts_dir` and audit its sibling `*.json`
    sidecar. For each failing chart, rename the PNG to `*.png.suspect` so
    the workflow's `_inline_charts` substitution picks a different chart
    for any prose reference. Returns a list of (png_path, reason) for
    surfacing on the report row."""
    if not charts_dir.exists():
        return []
    failed: list[tuple[Path, str]] = []
    for png in sorted(charts_dir.glob("*.png")):
        sidecar = png.with_suffix(".json")
        if not sidecar.exists():
            continue
        ok, reason = audit_one(sidecar)
        if ok:
            continue
        # Rename so _inline_charts doesn't see it as available. Keep the
        # sidecar so we can debug post-mortem.
        try:
            png.rename(png.with_suffix(png.suffix + ".suspect"))
        except OSError:
            log.exception("could not quarantine suspect chart %s", png)
            continue
        failed.append((png, reason or "title/data mismatch"))
        log.warning("chart audit: quarantined %s -- %s", png.name, reason)
    return failed


def format_report(failed: Iterable[tuple[Path, str]]) -> str:
    items = list(failed)
    if not items:
        return ""
    return "Chart audit quarantined " + "; ".join(
        f"{p.name} ({reason})" for p, reason in items
    )
