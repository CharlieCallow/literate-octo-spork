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
        prompt = f"""You're the Data & Charts agent on a Forte Research report. The brief and the analyst notes are below. Pick ONE chart that would land hardest, generate it via the make_chart tool (Forte house style is applied automatically), then write a short data section.

BRIEF:

{brief}

NOTES:

{notes}

Workflow:
1. Use fred_series first if you need to scout a series.
2. Call make_chart EXACTLY ONCE, with chart_kind='line' or 'bar', a title (12-15 words, descriptive), a subtitle (one short sentence, the angle), a clean filename like 'rates.png', and a series_id.
3. Then output a markdown section in your voice:
   - Start with `## Data & charts`.
   - Include `[chart: <filename>]` on its own line where the chart goes.
   - 150-250 words of commentary. Terse. Two sentences and a number. Drop one dry one-liner.

Do not make multiple charts in M1 — exactly one.
"""
        tools: list[Tool] = [fred_series_tool(), make_chart_tool(charts_dir)]
        return self.run(prompt, tools=tools, max_tokens=2048)
