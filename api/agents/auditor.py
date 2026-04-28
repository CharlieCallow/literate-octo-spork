"""Number-audit agent. Reads the edited prose and the tool outputs the analysts
saw during research, then re-emits the prose with any quantitative claim that
isn't grounded in a tool output either qualified ("around", "roughly") or
struck. Always Haiku — this is mechanical work, not reasoning."""

from __future__ import annotations

from api.agents.base import Agent, AgentResult
from api.agents.cost import CostTracker
from api.settings import settings


class Auditor(Agent):
    role = "auditor"
    # Haiku regardless of report mode -- the audit is a grounded find/replace
    # against a JSON ledger, not analysis.
    default_model = settings.model_haiku

    def __init__(self, cost: CostTracker, **kwargs: object) -> None:
        super().__init__(
            persona_path=settings.team_dir / "editor-in-chief.md",
            cost=cost,
            **kwargs,  # type: ignore[arg-type]
        )

    def review(self, *, edited: str, tool_outputs_jsonl: str) -> AgentResult:
        # Renamed from `audit` because Agent base class has a `self.audit`
        # callable hook attribute -- a method called `audit` on the instance
        # would shadow the method via Python's attribute resolution order.
        prompt = f"""You are auditing the numerical claims in a finished research report against the tool outputs the analysts saw during research. The goal: every number that appears in the prose should be grounded in the JSON ledger below. Numbers that aren't grounded must be either qualified or struck.

You are NOT editing prose, voice, structure, or argument. Only numbers and quantitative claims. If you can't find a number in the ledger, do not invent one — qualify or remove.

# TOOL OUTPUTS (JSONL — one tool call per line)

{tool_outputs_jsonl}

# EDITED REPORT

{edited}

Rules:
- A number is "grounded" if the same value (or a value that rounds to it) appears in any line of the JSONL ledger above. Round-trip tolerance: 1% relative for prices and rates, exact for percentages quoted to 1dp.
- Grounded numbers: leave the sentence unchanged.
- Ungrounded but plausible (close-ish to something in the ledger, or a reasonable derived figure like a percentage change of two grounded values): qualify with "around", "roughly", "approximately", "near" — keep the figure.
- Ungrounded and implausible (no related data in the ledger at all): rewrite the sentence to drop the figure. Do not insert a different number. Keep the surrounding voice.
- Dates, ticker symbols, FRED series ids and other non-quantitative identifiers are not subject to audit.

Output the FULL edited report verbatim, with the modifications above applied. Preserve every literal heading (`# OPENING`, `# HOUSE VIEW (TOP)`, `# REVISED SECTIONS`, `# DISAGREEMENT`, `# BEAR CASE`, `# HOUSE VIEW (BOTTOM)`, `# CLOSING`) — the workflow parses them.

After the report, append a single section:

# AUDIT NOTES
- <one bullet per change you made: file location (section heading), original phrasing, what you changed it to, and why>
<If you made zero changes, write "(none — all figures grounded)" and stop.>
"""
        return self.run(prompt, max_tokens=8192, max_iters=1)
