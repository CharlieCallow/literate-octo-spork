"""Persona voice-stat collection and drift detection.

After every report we compute a small set of voice metrics per contributing
persona (sentence length, hedge ratio, em-dashes per 1k words, conviction
density). Trailing windows of these power the drift alert: if a persona's
recent voice has flattened toward the firm-wide mean we surface it on the
team page so the user can patch the persona file before it gets worse.

Voice rot is the failure mode the spec calls out. Catching it early is
cheap; catching it after 20 reports of bland prose is not.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from sqlmodel import Session, select

from api.db import engine
from api.models import PersonaVoiceStat

# Hedge words tuned for the kind of analyst prose we see. Picked deliberately
# small -- false positives drag the ratio toward zero for everyone, false
# negatives mean we miss real drift.
_HEDGE_WORDS = (
    "perhaps", "maybe", "somewhat", "possibly", "might", "could be",
    "seems", "appears", "arguably", "potentially", "probably", "suggests",
    "indicates", "tends to", "may be", "kind of", "sort of",
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z(])")
_WORD_RE = re.compile(r"\b[\w'-]+\b")
# Both the proper em-dash (—) and ASCII " -- " (which the personas use).
_EMDASH_RE = re.compile(r"—|--")
# {c4} and {c5} tags before strip-pass; counts as the persona's high-conviction
# claim density. We don't count {c1}/{c2}/{c3} -- those are signal noise.
_HIGH_CONV_RE = re.compile(r"\{c[45]\}")


@dataclass
class VoiceMetrics:
    n_words: int
    mean_sentence_words: float
    hedge_ratio: float
    emdash_per_1k: float
    conviction_density: float


def compute(text: str) -> VoiceMetrics:
    """Compute one shot of voice stats from a persona's draft markdown.

    Operates on the analyst's pre-edit draft (post-tag-strip the data is
    gone; we want their voice, not the EIC's compression of it)."""
    if not text.strip():
        return VoiceMetrics(0, 0.0, 0.0, 0.0, 0.0)

    sentences = [s for s in _SENTENCE_SPLIT_RE.split(text.strip()) if s.strip()]
    n_sentences = max(len(sentences), 1)
    words = _WORD_RE.findall(text)
    n_words = max(len(words), 1)

    hedge_count = 0
    lower = text.lower()
    for w in _HEDGE_WORDS:
        hedge_count += lower.count(w)

    emdash_count = len(_EMDASH_RE.findall(text))
    high_conv_count = len(_HIGH_CONV_RE.findall(text))

    return VoiceMetrics(
        n_words=n_words,
        mean_sentence_words=n_words / n_sentences,
        hedge_ratio=hedge_count / n_sentences,
        emdash_per_1k=(emdash_count / n_words) * 1000,
        conviction_density=(high_conv_count / n_sentences) * 100,
    )


def record(report_id: int, persona_slug: str, metrics: VoiceMetrics) -> PersonaVoiceStat:
    """Persist a single (report, persona) row of voice stats."""
    with Session(engine) as session:
        row = PersonaVoiceStat(
            report_id=report_id,
            persona_slug=persona_slug,
            n_words=metrics.n_words,
            mean_sentence_words=metrics.mean_sentence_words,
            hedge_ratio=metrics.hedge_ratio,
            emdash_per_1k=metrics.emdash_per_1k,
            conviction_density=metrics.conviction_density,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row


def _trailing_avg(rows: Sequence[PersonaVoiceStat]) -> VoiceMetrics:
    if not rows:
        return VoiceMetrics(0, 0.0, 0.0, 0.0, 0.0)
    n = len(rows)
    return VoiceMetrics(
        n_words=int(sum(r.n_words for r in rows) / n),
        mean_sentence_words=sum(r.mean_sentence_words for r in rows) / n,
        hedge_ratio=sum(r.hedge_ratio for r in rows) / n,
        emdash_per_1k=sum(r.emdash_per_1k for r in rows) / n,
        conviction_density=sum(r.conviction_density for r in rows) / n,
    )


@dataclass
class DriftAlert:
    persona_slug: str
    axis: str           # which metric drifted -- e.g. "hedge_ratio"
    persona_value: float
    firm_value: float
    pct_drift: float    # signed, negative = persona below firm mean


# A persona's trailing-N average is "drifted" if it's within MIN_GAP_PCT of the
# firm-wide mean. We only check axes where the persona used to be distinctive.
PERSONA_WINDOW = 5
FIRM_WINDOW = 30
MIN_REPORTS = 5  # below this we don't have the data to alert
DRIFT_PCT = 0.20  # within 20% of firm mean = flattened


def detect_drift() -> list[DriftAlert]:
    """Return a list of personas whose voice has flattened toward the firm
    mean across recent reports. Empty list when there's not enough data."""
    with Session(engine) as session:
        all_rows = session.exec(
            select(PersonaVoiceStat).order_by(PersonaVoiceStat.created_at.desc())  # type: ignore[attr-defined]
        ).all()

    if len(all_rows) < MIN_REPORTS:
        return []

    firm_recent = list(all_rows)[:FIRM_WINDOW]
    firm = _trailing_avg(firm_recent)

    by_persona: dict[str, list[PersonaVoiceStat]] = {}
    for r in all_rows:
        by_persona.setdefault(r.persona_slug, []).append(r)

    alerts: list[DriftAlert] = []
    for slug, rows in by_persona.items():
        if len(rows) < MIN_REPORTS:
            continue
        persona_recent = rows[:PERSONA_WINDOW]
        if len(persona_recent) < MIN_REPORTS:
            continue
        persona = _trailing_avg(persona_recent)

        for axis in ("mean_sentence_words", "hedge_ratio", "emdash_per_1k", "conviction_density"):
            p_val = getattr(persona, axis)
            f_val = getattr(firm, axis)
            if f_val == 0:
                continue
            gap = abs(p_val - f_val) / max(abs(f_val), 1e-9)
            # Drift = persona was distinctive earlier but now sits close to firm mean.
            persona_baseline = _trailing_avg(rows[PERSONA_WINDOW:PERSONA_WINDOW + PERSONA_WINDOW])
            baseline_val = getattr(persona_baseline, axis)
            if baseline_val == 0:
                continue
            baseline_gap = abs(baseline_val - f_val) / max(abs(f_val), 1e-9)
            if baseline_gap > DRIFT_PCT and gap < DRIFT_PCT:
                pct_drift = (p_val - f_val) / abs(f_val)
                alerts.append(DriftAlert(
                    persona_slug=slug,
                    axis=axis,
                    persona_value=p_val,
                    firm_value=f_val,
                    pct_drift=pct_drift,
                ))
    return alerts
