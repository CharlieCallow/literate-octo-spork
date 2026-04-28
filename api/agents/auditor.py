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

    def pre_draft_audit(
        self, *, agent_slug: str, notes_md: str, tool_outputs_jsonl: str,
    ) -> AgentResult:
        """Per-analyst self-audit on research notes before they go to draft.

        Same logic as review() but scoped to one analyst's notes and the slice
        of the tool-output ledger that analyst produced. Catches ungrounded
        claims while the analyst still has time to either qualify them or have
        them killed in the draft stage. The rewritten notes overwrite the
        original notes-{slug}.md file."""
        # Filter the ledger to lines this analyst actually emitted. Keeps the
        # prompt small and prevents an analyst's claim getting "grounded" by
        # a tool output a different analyst saw.
        scoped: list[str] = []
        for line in tool_outputs_jsonl.splitlines():
            if not line.strip():
                continue
            if f'"agent": "{agent_slug}"' in line or f'"agent":"{agent_slug}"' in line:
                scoped.append(line)
        scoped_blob = "\n".join(scoped) if scoped else "(no tool calls recorded for this analyst)"

        prompt = f"""You are auditing an analyst's research notes BEFORE they draft from them. The same grounding logic the post-edit auditor applies, but moved upstream so weak claims die in research instead of leaking into prose.

You are NOT editing voice or argument. Only numbers and quantitative claims. If a number isn't grounded in the analyst's own tool calls below, qualify it ("around", "roughly") or strike the figure -- never invent a different one.

# THIS ANALYST'S TOOL CALLS (JSONL -- one call per line)

{scoped_blob}

# THEIR NOTES

{notes_md}

Rules:
- A number is "grounded" if the same value (or a value within 1% relative tolerance for prices/rates, exact for percentages quoted to 1dp) appears in any line of their JSONL above.
- Grounded numbers: leave the sentence unchanged.
- Ungrounded but plausible derivative (a percentage change of two grounded values, e.g.): qualify with "around" / "roughly" / "approximately" / "near" -- keep the figure.
- Ungrounded and implausible (no related data in this analyst's ledger): rewrite the sentence to drop the figure. Do not insert a different number. Keep the surrounding voice and structure.
- Dates, ticker symbols, FRED series ids, sponsor names, study NCT ids and other non-quantitative identifiers are not subject to audit.
- Markdown links (`[anchor](url)`) and conviction tags (`{{c1}}`..`{{c5}}`) MUST be preserved verbatim -- the downstream parsers need them.

Output the FULL notes verbatim with the modifications above applied. No headings before or after, no audit notes section -- the result feeds directly into the draft stage as if it were the analyst's own."""
        return self.run(prompt, max_tokens=4096, max_iters=1)

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
