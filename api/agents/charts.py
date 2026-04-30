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
   - chart_kind: 'line' (default), 'bar', 'regime' (auto-shades NBER recessions on line series; great for cycle context), 'comparison' (dual-axis -- pass compare_with), or 'event' (vertical lines at named dates -- pass events=[{{date, label}}, ...]).
   - source: 'fred' (US macro), 'yfinance' (prices), 'worldbank' (cross-country macro, annual), or 'eia' (US energy)
   - series_or_ticker: FRED id (e.g. 'DGS10'), yfinance ticker (e.g. 'SPY', '^VIX'), worldbank 'COUNTRY:INDICATOR' (e.g. 'GBR:NY.GDP.MKTP.KD.ZG'), or EIA route (e.g. 'petroleum/pri/spt/data')
   - title: 8-15 words, descriptive
   - subtitle: one short sentence, the angle
   - filename: short and unique like 'rates.png', 'spy.png'
   - For 'regime': pass shaded='nber' to overlay recession bands.
   - For 'comparison': pass compare_with to overlay a second series on a dual y-axis.
   - For 'event': pass events as a list of {{date, label}} objects (e.g. Fed meeting dates).
4. AFTER all make_chart calls succeed, write the markdown section. This is mandatory -- the agent ALWAYS finishes with the section, never with just a status line.
   - The VERY FIRST CHARACTERS of your final response must be `## Data & charts` -- no preamble, no "Both charts rendered, now the section", no acknowledgements. Anything before that heading lands directly in the PDF as visible text.
   - PICK A LOAD-BEARING CHART. Of the 2-3 charts you generated, exactly ONE is the chart that, if cut, would gut the note -- the chart that makes the asymmetry visible in one glance. Place that chart FIRST in the section. Its commentary paragraph must be 100-160 words and the FIRST sentence must literally start `**Load-bearing chart.** ` (bold marker included). The other charts get 50-90-word commentary, no marker.
   - For EACH chart you generated, write a short paragraph of commentary at the lengths above.
     Place `[chart: <exact-filename-you-passed-to-make_chart>]` on its own line at the START of each paragraph.
   - Use the EXACT filenames you passed to make_chart -- typos = missing charts.
   - Terse. Two sentences and a number per chart. Drop one dry one-liner across the section.
   - Total length 250-450 words.
5. SCENARIO TABLE -- conditional. If the brief or analyst notes identify a path-dependent thesis with one or more discrete forks (event A happens y/n; vote passes y/n; rate cut is 25 vs. 50; trust survives mark-up; etc.), append a scenario matrix at the END of the section under a `### Scenarios` subheading. This is the actual analytical work for any binary-fork thesis -- prose alone is not enough. Format:
   - A markdown table with columns: `Scenario | Probability | What it looks like | Implication`.
   - 2-4 rows covering the live forks. Probabilities are your honest call (must sum to ~100% if the rows are exhaustive); reason from base rates + the specific catalyst, not vibes.
   - Keep each cell terse (a phrase, not a paragraph). Numbers and levels where they help.
   - If the thesis is genuinely continuous / not fork-driven (e.g. a slow trend, a quality screen with no catalyst), write the literal phrase "_No discrete forks -- thesis is continuous._" under the `### Scenarios` heading and stop. Don't manufacture forks for the sake of the table.

Failure modes to avoid:
- Writing the section before calling make_chart -- leads to chart refs that don't exist.
- Saying "Three charts rendered. Now the data section." and stopping -- always write the actual section.
- Inventing filenames in the section that don't match the make_chart calls.
- Prefixing the section with conversational text -- starts with `## Data & charts` on line 1, period.
- Marking more than one chart as load-bearing, or marking none. Exactly one.
- Skipping the scenario table when the thesis is event-driven. If there's a catalyst tree, the table is mandatory.

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
        max_tokens = {ReportMode.fast: 2048, ReportMode.standard: 3584, ReportMode.deep: 4608}[mode]
        return self.run(prompt, tools=tools, max_tokens=max_tokens, max_iters=max_iters)
