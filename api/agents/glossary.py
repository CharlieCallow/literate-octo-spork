"""Glossary auto-builder.

After the editor finishes, a small Haiku pass scans the edited prose for
technical terms / acronyms / jargon a generalist reader wouldn't know,
and writes a one-line definition for each. The output renders as an
appendix section in the PDF + reading mode -- a real win on biotech /
credit / semis themes where every paragraph drops three undefined acronyms.

Always Haiku because this is mechanical extraction, not analysis. Skipped
in test mode (the smoke pipeline doesn't render the appendix anyway since
charts/data-section/etc are also off)."""

from __future__ import annotations

from api.agents.base import Agent, AgentResult
from api.agents.cost import CostTracker
from api.settings import settings


class Glossary(Agent):
    role = "glossary"
    default_model = settings.model_haiku

    def __init__(self, cost: CostTracker, **kwargs: object) -> None:
        # Reuse the EIC persona file -- the role is editorial cleanup,
        # not a distinct voice. Pinned to Haiku regardless of mode.
        super().__init__(
            persona_path=settings.team_dir / "editor-in-chief.md",
            cost=cost,
            **kwargs,  # type: ignore[arg-type]
        )

    def build(self, *, edited_prose: str) -> AgentResult:
        prompt = f"""You are writing a Glossary appendix for a Forte Research report. Read the edited prose and identify terms a generalist reader wouldn't know on first appearance: acronyms ("HBM", "TAM", "BAUFV"), domain jargon ("operator margin", "ASO", "convoyance"), regulatory codes ("ITC", "PJM"), drug names, niche tickers used non-obviously, etc.

For each term: one short definition that earns its keep -- the kind a smart reader skims and goes "ok, got it" without slowing down. No academic bloat.

Skip:
- Anything any educated reader knows already (CPI, GDP, ETF, Fed, S&P 500, EU, USD, Q3).
- Common company names (Apple, Nvidia, Pfizer).
- Terms the prose itself defines inline (no point repeating).
- Anything that appears once in passing -- only define what matters to the report's argument.

Output exactly this structure (the report renderer parses it as markdown):

# Glossary

**TERM** — one-line definition.
**TERM** — one-line definition.
...

8-15 entries is the sweet spot. If the prose has fewer than 5 terms worth defining, output exactly:

(none)

so the renderer knows to skip the appendix.

PROSE:

{edited_prose}
"""
        return self.run(prompt, max_tokens=1024, max_iters=1)


def is_empty(text: str) -> bool:
    """True if the model returned the literal "(none)" sentinel or empty
    output. Saves the renderer from appending a heading-only appendix."""
    stripped = text.strip().lower()
    if not stripped:
        return True
    return "(none)" in stripped and len(stripped) < 60
