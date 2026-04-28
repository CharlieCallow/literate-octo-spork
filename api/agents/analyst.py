"""Generic analyst. Macro / equity flavours selected by persona file at runtime."""

from __future__ import annotations

from pathlib import Path

from api.agents.base import Agent, AgentResult, Tool
from api.agents.cost import CostTracker
from api.agents.tools import (
    edgar_filings_tool,
    fred_series_tool,
    web_search_tool,
    wikipedia_tool,
    yfinance_history_tool,
)
from api.models import ReportMode
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

    def research(
        self,
        brief: str,
        theme: str,
        working_dir: Path,
        *,
        mode: ReportMode = ReportMode.standard,
    ) -> AgentResult:
        prompt = f"""You're a contributing analyst on a Forte Research report. The Editor-in-Chief's brief is below.

BRIEF:

{brief}

THEME: {theme}

Run your research. Available tools:
- `fred_series` — macro time series (rates, CPI, employment, etc.)
- `yfinance_history` — equity / ETF / FX / crypto / futures price history
- `wikipedia_summary` — definitional and background context
- `edgar_filings` — list recent SEC filings for a ticker
- `web_search` — current news, headlines, broker notes (Anthropic-managed)

Use whichever tools fit your beat. Stay in your voice. Write structured notes in markdown -- claims with evidence and sources. Cite inline: when a claim rests on a specific source, link it like `[short anchor text](https://exact-url)` so the renderer can turn it into a numbered footnote. Use real URLs from your tool results, never invent them. Be opinionated; hedging without conviction is the failure mode. Output ~300-500 words.
"""
        tools: list[Tool] = [
            fred_series_tool(),
            yfinance_history_tool(),
            wikipedia_tool(),
            edgar_filings_tool(),
        ]
        # Per-mode tuning. fast: drop web search (biggest cost driver) and
        # tighten loop. deep: bigger token + iter budget for thorough research.
        server_tools = [] if mode == ReportMode.fast else [web_search_tool()]
        max_iters = {ReportMode.fast: 4, ReportMode.standard: 6, ReportMode.deep: 10}[mode]
        max_tokens = {ReportMode.fast: 2048, ReportMode.standard: 3072, ReportMode.deep: 4096}[mode]
        return self.run(
            prompt,
            tools=tools,
            server_tools=server_tools,
            max_tokens=max_tokens,
            max_iters=max_iters,
        )

    def draft(self, brief: str, notes: str, theme: str) -> AgentResult:
        prompt = f"""Draft your section of the report based on the notes below. Stay in your voice (the persona file is your identity). The Editor will preserve voice when editing -- write in the voice you actually want to read.

BRIEF:

{brief}

YOUR NOTES:

{notes}

Output a single section in markdown. Start with a brief inline header (## <Section heading>). 200-400 words. Reference charts inline as `[chart: <filename>]` if you want one rendered (Data & Charts will produce them). Cite real sources inline using markdown links `[anchor text](https://url)` -- the renderer turns these into numbered footnotes. Do not invent data or URLs -- only use figures and links from your notes.
"""
        return self.run(prompt, max_tokens=2048)
