"""Devil's Advocate / red-team agent. Reads the brief and the analyst drafts,
returns the strongest counter-thesis. Runs after `draft` and before `edit` so
the EIC has the bear case in hand when they integrate the final report."""

from __future__ import annotations

from api.agents.base import Agent, AgentResult
from api.agents.cost import CostTracker
from api.settings import settings


class RedTeam(Agent):
    role = "devils-advocate"
    default_model = settings.model_sonnet

    def __init__(self, cost: CostTracker, **kwargs: object) -> None:
        super().__init__(
            persona_path=settings.team_dir / "devils-advocate.md",
            cost=cost,
            **kwargs,  # type: ignore[arg-type]
        )

    def critique(
        self,
        *,
        brief: str,
        sections: list[dict[str, str]],
        chart_summary: str,
    ) -> AgentResult:
        sec_blob = "\n\n---\n\n".join(
            f"## {s['author']} — {s['role']}\n\n{s['body']}"
            for s in sections
        )
        prompt = f"""You're Saoirse Mok, this firm's standing bear. The team has finished its drafts. Before the EIC integrates them, you write the counter-thesis.

BRIEF:

{brief}

ANALYST SECTIONS:

{sec_blob}

CHARTS PRODUCED:

{chart_summary}

Return three sections in markdown using EXACTLY these literal headings (the workflow parses them):

# WHERE THIS REPORT IS WRONG
<2-3 short paragraphs. The single strongest objection — the one that, if true, changes the trade. Be specific: name the claim, name the assumption it rests on, name the data that would falsify it.>

# WHAT'S ALREADY PRICED IN
<one paragraph. The part of the thesis the market already believes. Not worth writing. Cut it from the next draft.>

# THE TRADE WE'RE MISSING
<optional. One paragraph. If the report's framing has ignored a better expression of the same view (e.g. they're long the obvious thing when the financing channel is the trade), name it. If nothing fits, write "(none)" and stop.>

Stay in your voice. Surgical, dry, slightly mean. Hedging is a failure mode — the EIC handles nuance, you handle the objection. ~250-400 words total.
"""
        return self.run(prompt, max_tokens=1536, max_iters=1)
