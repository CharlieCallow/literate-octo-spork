"""Stylist — final layout polish before render.

A Haiku pass that scans the edited prose (chart tags inlined as
`[chart: filename.png]`) and inserts `<div class="page-break"></div>`
markers at points where a forced break would tidy the rendered PDF.

Mechanical only — no rewrites, no edits, no commentary. If no breaks
help, returns the prose unchanged. Best-effort: any failure leaves the
prose untouched (like the Glossary pass)."""

from __future__ import annotations

from api.agents.base import Agent, AgentResult
from api.agents.cost import CostTracker
from api.settings import settings


PAGE_BREAK_TAG = '<div class="page-break"></div>'


class Stylist(Agent):
    role = "stylist"
    default_model = settings.model_haiku

    def __init__(self, cost: CostTracker, **kwargs: object) -> None:
        super().__init__(
            persona_path=settings.team_dir / "stylist.md",
            cost=cost,
            **kwargs,  # type: ignore[arg-type]
        )

    def polish(self, *, edited_prose: str) -> AgentResult:
        prompt = f"""You are the Stylist. The prose below will be rendered to A4 PDF. Your only job is to insert {PAGE_BREAK_TAG!r} markers where a forced page break would tidy the layout.

Rules:
- Output the prose verbatim, unchanged in wording, with at most THREE page-break divs inserted at natural boundaries (between paragraphs, never mid-paragraph, never inside a chart tag).
- A break is warranted when:
  * A `[chart: ...]` tag is preceded by a short paragraph that would leave a half-page gap before the chart pushes to the next page.
  * Two consecutive top-level headings (`## ...`) sit close together and the second one would orphan at the bottom of a page.
- A break is NOT warranted between every section. Most reports need zero or one break. If unsure, insert nothing.
- Do not edit, reword, reorder, or remove any prose, chart tag, heading, or callout. Do not add commentary.
- Output ONLY the prose (with breaks inserted if any). No preamble, no explanation.

PROSE:

{edited_prose}
"""
        return self.run(prompt, max_tokens=8192, max_iters=1)


def merge(original: str, stylist_output: str) -> str:
    """Accept the stylist's output only if it's the original prose plus
    inserted `<div class="page-break"></div>` markers — nothing else
    changed. If the model rewrote anything, fall back to the original.

    The check: stripping all page-break divs from the stylist output
    must yield the original prose (modulo whitespace)."""
    stripped = stylist_output.replace(PAGE_BREAK_TAG, "")
    if _normalise(stripped) == _normalise(original):
        return stylist_output
    return original


def _normalise(s: str) -> str:
    return "\n".join(line.rstrip() for line in s.strip().splitlines())
