"""Brief-coverage check.

After research, a Haiku pass compares the brief's # QUESTIONS list to the
concatenated analyst notes and reports per-question coverage:
  - answered: a note addresses the question with evidence
  - partial: the question is touched on but not fully answered
  - missing: no analyst returned to it

Output is structured markdown read by the EIC at edit time -- gaps land in
the editor's prompt as UNANSWERED BRIEF QUESTIONS so the closing doesn't
promise answers the body never delivered. Always Haiku; this is mechanical
matching, not analysis."""

from __future__ import annotations

import re

from api.agents.base import Agent, AgentResult
from api.agents.cost import CostTracker
from api.settings import settings


class Coverage(Agent):
    role = "coverage"
    default_model = settings.model_haiku

    def __init__(self, cost: CostTracker, **kwargs: object) -> None:
        # Reuse the EIC persona file -- same job-shape (editorial discipline),
        # but pinned to Haiku because this is mechanical matching.
        super().__init__(
            persona_path=settings.team_dir / "editor-in-chief.md",
            cost=cost,
            **kwargs,  # type: ignore[arg-type]
        )

    def check(self, *, brief: str, notes: str) -> AgentResult:
        prompt = f"""You're checking that the analyst notes returned to every question the brief asked. The brief lists numbered QUESTIONS; the notes follow. Map each question to its coverage in the notes.

BRIEF:

{brief}

NOTES (concatenated across analysts):

{notes}

For each numbered question in the brief's # QUESTIONS section, output exactly one bullet using this literal shape -- the workflow parses it:

- Q<n>: <answered|partial|missing> — <one short anchor: a phrase from the notes if answered/partial, or "no analyst returned to it" if missing>

Rules:
- "answered": at least one analyst's notes contain a substantive claim with evidence on this question.
- "partial": touched on but no concrete claim or no source backing it.
- "missing": nothing in the notes addresses it.
- Be strict. A passing mention without numbers or sources is partial, not answered.
- Output bullets in question order. Nothing else -- no preamble, no headings, no commentary.
"""
        return self.run(prompt, max_tokens=512, max_iters=1)


_BULLET_RE = re.compile(
    r"^\s*-\s*Q(?P<n>\d+)\s*:\s*(?P<status>answered|partial|missing)\s*[—\-:]\s*(?P<anchor>.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_QUESTION_RE = re.compile(r"^\s*(?P<n>\d+)\.\s+(?P<text>.+?)\s*$", re.MULTILINE)


def parse_questions(brief: str) -> list[tuple[int, str]]:
    """Pull '1. text\\n2. text\\n...' out of the brief's # QUESTIONS section."""
    m = re.search(r"^# QUESTIONS\s*\n(.+?)(?=\n# |\Z)", brief, re.S | re.M)
    if not m:
        return []
    block = m.group(1)
    return [(int(qm.group("n")), qm.group("text").strip()) for qm in _QUESTION_RE.finditer(block)]


def parse_coverage(text: str) -> list[dict[str, str]]:
    """Parse the bullet output into [{n, status, anchor}, ...]."""
    out: list[dict[str, str]] = []
    for m in _BULLET_RE.finditer(text):
        out.append({
            "n": m.group("n"),
            "status": m.group("status").lower(),
            "anchor": m.group("anchor").strip(),
        })
    return out


def gaps(brief: str, coverage_text: str) -> list[str]:
    """Return human-readable list of unanswered (missing) brief questions.

    "partial" is intentionally not surfaced as a gap -- the editor should
    decide whether to push for more evidence or accept the partial answer.
    Hard misses are unambiguous failures of the research stage and worth
    surfacing into the EIC edit prompt verbatim."""
    questions = dict(parse_questions(brief))
    rows = parse_coverage(coverage_text)
    out: list[str] = []
    for r in rows:
        if r["status"] != "missing":
            continue
        try:
            qn = int(r["n"])
        except ValueError:
            continue
        text = questions.get(qn)
        if text:
            out.append(f"Q{qn}: {text}")
    return out
