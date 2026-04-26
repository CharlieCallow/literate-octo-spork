"""Generic analyst. Macro / equity flavours selected by persona file at runtime."""

from __future__ import annotations

from pathlib import Path

from api.agents.base import Agent, AgentResult, Tool
from api.agents.cost import CostTracker
from api.agents.tools import fred_series_tool
from api.settings import settings


class Analyst(Agent):
    role = "analyst"
    default_model = settings.model_sonnet

    def __init__(
        self,
        persona_filename: str,
        cost: CostTracker,
        **kwargs: object,
    ) -> None:
        super().__init__(
            persona_path=settings.team_dir / persona_filename,
            cost=cost,
            **kwargs,  # type: ignore[arg-type]
        )

    def research(self, brief: str, theme: str, working_dir: Path) -> AgentResult:
        prompt = f"""You're a contributing analyst on a Forte Research report. The Editor-in-Chief's brief is below.

BRIEF:

{brief}

THEME: {theme}

Run your research. Use the fred_series tool to pull any macro time series you need. Write structured notes in markdown — claims with evidence and sources. Be opinionated; the report has a take, hedging without conviction is the failure mode. Output ~300-500 words.
"""
        tools: list[Tool] = [fred_series_tool()]
        return self.run(prompt, tools=tools, max_tokens=2048)

    def draft(self, brief: str, notes: str, theme: str) -> AgentResult:
        prompt = f"""Draft your section of the report based on the notes below. Stay in your voice (the persona file is your identity). The Editor will preserve voice when editing — write in the voice you actually want to read.

BRIEF:

{brief}

YOUR NOTES:

{notes}

Output a single section in markdown. Start with a brief inline header (## <Section heading>). 200-400 words. Reference charts inline as `[chart: <filename>]` if you want one rendered (Data & Charts will produce them). Do not invent data — only use figures from your notes or charts you've explicitly requested.
"""
        return self.run(prompt, max_tokens=2048)
