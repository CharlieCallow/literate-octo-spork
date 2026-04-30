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

    def polish_with_layout(
        self,
        *,
        edited_prose: str,
        layout_report: str,
    ) -> AgentResult:
        """Layout-aware pass. Runs AFTER the first render: takes the
        rendered PDF's per-page layout summary and inserts page-break
        divs at points that will tidy the second render."""
        prompt = f"""You are the Stylist. A draft of this report has already been rendered to A4 PDF; the layout report below lists pages with notable issues (e.g. a large blank gap at the bottom because the next block -- usually a chart -- couldn't fit and pushed to the next page).

Your only job is to insert {PAGE_BREAK_TAG!r} markers in the prose so that the next render closes those gaps. A page break div placed BEFORE a paragraph forces the prose preceding it onto its own page; the paragraph after the div then starts at the top of a new page (which is fine when you're already going to push there anyway, and lets you place the break earlier so the previous page fills with text).

A more useful technique here: identify the paragraph that gets pushed (the one starting the next page after a gap) and insert the break right before it -- so the gap is intentional rather than accidental. Even better, find a SHORTER preceding paragraph and start the new page from there, leaving a fuller previous page.

Rules:
- Output the prose verbatim, unchanged in wording, with at most THREE page-break divs inserted at paragraph boundaries (never mid-paragraph, never inside a chart tag, never inside a callout block).
- Place each break on its own line between two paragraphs.
- Do not edit, reword, reorder, or remove any prose, chart tag, heading, or callout. Do not add commentary.
- If the layout report says "(no notable layout issues detected)", output the prose unchanged.
- Output ONLY the prose (with breaks inserted if any). No preamble, no explanation.

LAYOUT REPORT:

{layout_report}

PROSE:

{edited_prose}
"""
        return self.run(prompt, max_tokens=16384, max_iters=1)


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
