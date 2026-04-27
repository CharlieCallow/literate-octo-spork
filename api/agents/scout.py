"""Scout -- daily theme digest. Cheap-tier model, web-search heavy."""

from __future__ import annotations

from api.agents.base import Agent, AgentResult, Tool
from api.agents.cost import CostTracker
from api.agents.tools import hn_search_tool, reddit_hot_tool, web_search_tool
from api.settings import settings

DEFAULT_SUBREDDITS = ("wallstreetbets", "stocks", "investing", "SecurityAnalysis", "options", "Bogleheads")


class Scout(Agent):
    role = "scout"
    default_model = settings.model_haiku

    def __init__(self, cost: CostTracker, **kwargs: object) -> None:
        super().__init__(
            persona_path=settings.team_dir / "scout.md",
            cost=cost,
            **kwargs,  # type: ignore[arg-type]
        )

    def run_daily_digest(self, recent_headlines: list[str] | None = None) -> AgentResult:
        prior = "\n".join(f"- {h}" for h in (recent_headlines or [])[:30])
        subs = ", ".join(DEFAULT_SUBREDDITS)
        prompt = f"""You are running the daily morning scan for Forte Research. Surface the 10 most interesting themes for today's note: things that are moving, things the buyside is talking about, things that are underpriced or just-starting narratives.

You have three scan tools:
- `web_search` (Anthropic-managed) -- broad news, broker notes, primary sources.
- `reddit_hot` -- sentiment + what's actually being discussed. Suggested subs: {subs}.
- `hn_search` -- tech-adjacent themes (semis, AI, crypto, biotech, regulation).

Use a mix. Don't lean only on web_search.

AVOID REPEATING THESE RECENTLY SURFACED THEMES:
{prior or "(no prior themes -- this is your first run)"}

Output EXACTLY 10 themes. Use this format with literal headings -- the workflow parses them:

# THEME 1
**headline:** <one-line headline in your voice -- punchy, FOMO-aware, specific>
**why_now:** <one sentence on what's moving today/this week and why it matters>
**dig_into:** <one short line on what to check or who to listen to>
**sources:** <comma-separated URLs, 1-3 per theme, mix of web/reddit/hn>

# THEME 2
...

Stay in your voice (rapid-fire, headline-driven, slang where earned). Be opinionated -- a theme without a take is a wasted slot.
"""
        tools: list[Tool] = [reddit_hot_tool(), hn_search_tool()]
        return self.run(
            prompt,
            tools=tools,
            server_tools=[web_search_tool(max_uses=10)],
            max_tokens=4096,
            max_iters=12,
        )
