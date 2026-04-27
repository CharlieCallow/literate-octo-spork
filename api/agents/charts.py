"""Data & Charts. Picks visualisations, generates them, writes commentary."""

from __future__ import annotations

from pathlib import Path

from api.agents.base import Agent, AgentResult, Tool
from api.agents.cost import CostTracker
from api.agents.tools import (
    fred_series_tool,
    make_chart_tool,
    yfinance_history_tool,
)
from api.models import ReportMode
from api.settings import settings


class DataAndCharts(Agent):
    role = "data-and-charts"
    default_model = settings.model_sonnet

    def __init__(self, cost: CostTracker, **kwargs: object) -> None:
        super().__init__(
            persona_path=settings.team_dir / "data-and-charts.md",
            cost=cost,
            **kwargs,  # type: ignore[arg-type]
        )

    def build(
        self,
        brief: str,
        notes: str,
        charts_dir: Path,
        *,
        mode: ReportMode = ReportMode.standard,
    ) -> AgentResult:
        prompt = f"""You're the Data & Charts agent on a Forte Research report. The brief lists chart ideas under '# CHARTS' and the analyst notes are below. Generate 2-3 charts that land hardest, then write the data section.

BRIEF:

{brief}

ANALYST NOTES:

{notes}

Workflow -- IN THIS ORDER:

1. Read the brief's CHARTS section. Pick 2-3 charts (no more, no less).
2. Use `fred_series` or `yfinance_history` to scout series before charting them, ONLY if you need to verify a series exists.
3. Call `make_chart` 2-3 times -- ONCE PER CHART. Each call must succeed before you reference the file.
   - chart_kind: 'line' or 'bar'
   - source: 'fred' or 'yfinance'
   - series_or_ticker: real FRED id (e.g. 'DGS10') or yfinance ticker (e.g. 'SPY', '^VIX')
   - title: 8-15 words, descriptive
   - subtitle: one short sentence, the angle
   - filename: short and unique like 'rates.png', 'spy.png'
4. AFTER all make_chart calls succeed, write the markdown section. This is mandatory -- the agent ALWAYS finishes with the section, never with just a status line.
   - Start with `## Data & charts`.
   - For EACH chart you generated, write a short paragraph (60-120 words) of commentary.
     Place `[chart: <exact-filename-you-passed-to-make_chart>]` on its own line at the START of each paragraph.
   - Use the EXACT filenames you passed to make_chart -- typos = missing charts.
   - Terse. Two sentences and a number per chart. Drop one dry one-liner across the section.
   - Total length 200-400 words.

Failure modes to avoid:
- Writing the section before calling make_chart -- leads to chart refs that don't exist.
- Saying "Three charts rendered. Now the data section." and stopping -- always write the actual section.
- Inventing filenames in the section that don't match the make_chart calls.

Do not invent data. Every claim cites a number from a series you actually fetched.
"""
        tools: list[Tool] = [
            fred_series_tool(),
            yfinance_history_tool(),
            make_chart_tool(charts_dir),
        ]
        # Iter budget needs to cover scout + 2-3 make_chart calls + a final
        # answer block. Older budgets were tight; bumping so the agent can
        # finish the section after generating charts.
        max_iters = {ReportMode.fast: 8, ReportMode.standard: 12, ReportMode.deep: 16}[mode]
        max_tokens = {ReportMode.fast: 2048, ReportMode.standard: 3072, ReportMode.deep: 4096}[mode]
        return self.run(prompt, tools=tools, max_tokens=max_tokens, max_iters=max_iters)
