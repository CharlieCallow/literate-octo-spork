"""Data & Charts. Picks visualisations, generates them, writes commentary."""

from __future__ import annotations

from pathlib import Path

from api.agents.base import Agent, AgentResult, Tool
from api.agents.cost import CostTracker
from api.agents.tools import fred_series_tool, make_chart_tool
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

    def build(self, brief: str, notes: str, charts_dir: Path) -> AgentResult:
        prompt = f"""You're the Data & Charts agent on a Forte Research report. The brief lists chart ideas under '# CHARTS' and the analyst notes are below. Generate 2-3 charts that land hardest, then write the data section.

BRIEF:

{brief}

ANALYST NOTES:

{notes}

Workflow:

1. Read the brief's CHARTS section. Generate 2-3 charts (no more, no less). Use fred_series first if you need to scout a series.

2. Call make_chart 2-3 times, once per chart. For each:
   - chart_kind: 'line' or 'bar'
   - series_id: a FRED series ID
   - title: 8-15 words, descriptive
   - subtitle: one short sentence, the angle
   - filename: short and unique, e.g. 'rates.png', 'cpi.png', 'unemployment.png'

3. Then output a markdown section in your voice:
   - Start with `## Data & charts`.
   - For EACH chart you generated, write a short paragraph (60-120 words) of commentary.
     Place `[chart: <filename>]` on its own line at the START of each paragraph so the chart appears above its commentary.
   - Terse. Two sentences and a number per chart. Drop one dry one-liner across the section.
   - Total length 200-400 words.

Do not invent data. Every claim cites a number from a FRED series you actually fetched.
"""
        tools: list[Tool] = [fred_series_tool(), make_chart_tool(charts_dir)]
        return self.run(prompt, tools=tools, max_tokens=3072, max_iters=10)
