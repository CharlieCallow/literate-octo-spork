"""Temp-specialist registry.

When the EIC's brief flags an ad-hoc specialist, the Recruiter doesn't always
need to spawn a fresh persona -- if a previous report already hired someone
who covered the same ground, reuse them. Keeps the firm's roster
biographically coherent (same Yelena Park keeps showing up on semis reports
instead of a fresh ghost each time).

Match heuristic: bag-of-words Jaccard similarity between the EIC's request
and the candidate's name + role + bio. Cheap, deterministic, no LLM call.
A score above MATCH_THRESHOLD reuses the existing persona; below it the
Recruiter spawns fresh as before."""

from __future__ import annotations

import logging
import re

from sqlmodel import Session, select

from api.db import engine
from api.models import Persona, PersonaStatus

log = logging.getLogger("specialist-registry")

# Words that don't help match a topic. Tuned for the kind of requests the
# EIC writes ("Need a clinical-trial specialist for biotech readouts").
_STOPWORDS = {
    "a", "an", "and", "the", "for", "to", "of", "in", "on", "with", "by",
    "is", "are", "be", "this", "that", "their", "there", "we", "our",
    "specialist", "analyst", "expert", "covers", "covering", "coverage",
    "needs", "need", "needed", "required", "report", "team", "role",
    "ad", "hoc", "ad-hoc", "temp", "temporary", "research",
}

_TOKEN_RE = re.compile(r"[a-z0-9]+")
MATCH_THRESHOLD = 0.18  # Jaccard score; tuned on synthetic examples


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS and len(t) > 2}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def find_match(brief_request: str) -> Persona | None:
    """Return the best-matching archived or temp persona for this request,
    or None if nothing scores above MATCH_THRESHOLD."""
    needle = _tokens(brief_request)
    if not needle:
        return None

    with Session(engine) as session:
        candidates = list(session.exec(
            select(Persona).where(
                Persona.status.in_([PersonaStatus.archived, PersonaStatus.temp])  # type: ignore[union-attr,attr-defined]
            )
        ).all())

    best_score = 0.0
    best: Persona | None = None
    for p in candidates:
        haystack = _tokens(f"{p.name} {p.role} {p.markdown}")
        score = _jaccard(needle, haystack)
        if score > best_score:
            best_score = score
            best = p

    if best is None or best_score < MATCH_THRESHOLD:
        return None
    log.info(
        "registry hit for %r: matched %r (status=%s) score=%.2f",
        brief_request, best.slug, best.status, best_score,
    )
    return best
