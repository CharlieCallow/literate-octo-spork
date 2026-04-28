"""Auto-thread agent. Generates a 5-tweet thread summary of a published
report -- distilled from the OPENING + HOUSE VIEW (BOTTOM). Ships as a
'copy this and paste into X / LinkedIn' artifact, not posted automatically.

Always Haiku. The cost is ~$0.001 per thread; we run it every report."""

from __future__ import annotations

from api.agents.base import Agent, AgentResult
from api.agents.cost import CostTracker
from api.settings import settings


class Threader(Agent):
    role = "threader"
    default_model = settings.model_haiku

    def __init__(self, cost: CostTracker, **kwargs: object) -> None:
        super().__init__(
            persona_path=settings.team_dir / "editor-in-chief.md",
            cost=cost,
            **kwargs,  # type: ignore[arg-type]
        )

    def thread(self, *, theme: str, opening: str, house_view_bottom: str) -> AgentResult:
        prompt = f"""You're distilling a Forte Research report into a 5-tweet thread for X / LinkedIn. Use the editor's voice (dry, senior, slightly impatient, opinion-forward). The thread should make the reader want the full report, not replace it.

REPORT THEME: {theme}

OPENING (the editor's framing of the report):

{opening}

BOTTOM-LINE (the firm's takeaway):

{house_view_bottom}

Output exactly 5 tweets, numbered 1/ through 5/, separated by blank lines. Each tweet must be ≤280 characters including the number prefix. Structure:

1/ The hook -- one sentence that makes the reader stop scrolling. State the thesis directly, no question marks.
2/ The setup -- the regime / context the call sits inside. One specific number or named driver.
3/ The argument -- the strongest single piece of evidence.
4/ The trade -- what someone holding this view actually does. Asset + direction + rough horizon.
5/ The hedge -- where you'd be wrong. End with "Full report in comments." or similar.

No emojis. No hashtags. No "🧵". No "1/n" suffix. Plain text only.
"""
        return self.run(prompt, max_tokens=1024, max_iters=1)
