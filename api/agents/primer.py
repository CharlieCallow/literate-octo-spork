"""Pre-brief data primer.

Runs before the EIC writes the brief. A cheap Haiku reconnaissance pass
that pulls a handful of representative tool calls -- one or two FRED prints,
a ticker, a headline scan -- and writes a short "what the data says today"
memo. The EIC consumes that memo when writing the brief, so briefs are
anchored to what the tape actually shows instead of the model's training-time
priors. Side effect: analysts stop rediscovering basic facts in the research
stage.

Persona is shared with the Scout (rapid-fire, headline-driven). Cheap by design
-- 1-2 cents per report -- so it's worth running on every theme."""

from __future__ import annotations

from api.agents.base import Agent, AgentResult, Tool
from api.agents.cost import CostTracker
from api.agents.tools import (
    fred_series_tool,
    gdelt_tool,
    web_search_tool,
    wikipedia_tool,
    yfinance_history_tool,
)
from api.settings import settings


class Primer(Agent):
    role = "primer"
    # Always Haiku -- this is a cheap scan, not analysis. The memo only
    # has to be specific enough to anchor the brief.
    default_model = settings.model_haiku

    def __init__(self, cost: CostTracker, **kwargs: object) -> None:
        # Reuse the Scout's persona file -- same voice, same job shape.
        super().__init__(
            persona_path=settings.team_dir / "scout.md",
            cost=cost,
            **kwargs,  # type: ignore[arg-type]
        )

    def primer(self, theme: str, *, subtitle: str | None = None) -> AgentResult:
        sub = f" -- {subtitle}" if subtitle else ""
        prompt = f"""You're running a 30-second data reconnaissance pass before the EIC writes the brief on a new Forte Research theme. Pull a handful of representative numbers and headlines so the EIC isn't writing the brief blind.

THEME: {theme}{sub}

Workflow -- do this fast:
1. Pick 2-3 quantities that would anchor the theme (e.g. for "nuclear renaissance": U3O8 spot, CCJ price, electricity demand growth; for "GLP-1 second-order": NVO/LLY price action, semaglutide volumes if you can find them, US calorie consumption proxies). Pull each via the appropriate tool.
2. Run ONE web_search and/or ONE gdelt_news query for the most recent headlines on the theme.
3. Optionally: one wikipedia_summary if a foundational concept needs anchoring (a drug class, a regulatory regime, a commodity).
4. Stop. Don't keep digging -- the analysts do that later.

Output a short memo in markdown using EXACTLY these headings (the workflow parses them):

# WHAT THE TAPE SAYS
<3-5 bullet points. Each bullet: one specific number with its source and unit. e.g. "10Y at 4.32% (FRED DGS10, 2026-04-25)" or "CCJ +18% YTD, 35.40 last (Yahoo)". No commentary -- just the print.>

# WHAT'S MOVING THIS WEEK
<2-3 bullets. Each one a headline or development from the last few days, with the source url. Be specific about what changed and when.>

# OPEN QUESTIONS THE BRIEF SHOULD ADDRESS
<2-3 questions the EIC should make sure the brief answers. Tied to the tape above -- not generic. e.g. "Is the move in CCJ a re-rating or a reaction to one Kazatomprom headline?" not "What's the outlook for nuclear?".>

Stay terse. This is a primer, not a report. ~150-250 words total.
"""
        tools: list[Tool] = [
            fred_series_tool(),
            yfinance_history_tool(),
            wikipedia_tool(),
            gdelt_tool(),
        ]
        return self.run(
            prompt,
            tools=tools,
            server_tools=[web_search_tool(max_uses=2)],
            max_tokens=1024,
            max_iters=6,
        )
