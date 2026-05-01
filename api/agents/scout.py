"""Scout -- daily theme digest. Cheap-tier model, web-search heavy.

Also owns the source-quality discipline shared across the research pipeline:
the primary-source-only retry brief used by the audit gate when a report
falls below the 30% primary-source floor (see api/citations.py and the
audit stage in api/workflow/state_machine.py)."""

from __future__ import annotations

from api.agents.base import Agent, AgentResult, Tool
from api.agents.cost import CostTracker
from api.agents.tools import hn_search_tool, reddit_hot_tool, web_search_tool
from api.settings import settings

DEFAULT_SUBREDDITS = ("wallstreetbets", "stocks", "investing", "SecurityAnalysis", "options", "Bogleheads")

# Minimum share of citations that must come from primary sources (regulators,
# issuer IR, standards bodies, research papers). The audit stage rejects any
# report that lands below this and routes back to research with the retry
# brief built by primary_source_only_retry_brief().
PRIMARY_SOURCE_MIN_SHARE: float = 0.30

# Citation-discipline directive. Spliced into the analyst's research prompt
# so every research call carries the same rules; also the spine of the
# retry brief returned to the research stage when the audit gate fails.
CITATION_DISCIPLINE_DIRECTIVE: str = """\
SOURCE QUALITY -- HARD RULES:

- If you cite a measurement, regulatory filing, or vendor product, you must
  locate the primary source URL or remove the citation. "Inline" / "as
  reported" / "per coverage" is not a citation.
- IEEE / standards-body claims (e.g. IEEE 802.3 measurements): cite the
  paper title, working group meeting, and date, with a link to the
  ieeexplore.ieee.org entry, the standards.ieee.org page, or the
  ofcconference.org / opg.optica.org session/paper number.
- Vendor product claims (e.g. Broadcom Bailly, Marvell Aquila, Cisco Silicon
  One): cite the company's own press release URL on the IR / newsroom
  domain, or the SEC 8-K announcing it. Not a re-print on a tech blog.
- Regulatory data: cite the regulator's primary deep link
  (sec.gov/EDGAR document URL, fred.stlouisfed.org series page,
  fda.gov label page, eia.gov series, etc.) -- never the homepage and
  never a secondary aggregator's view of the same number.
- Research papers (arxiv / Nature / Science / NEJM / IEEE / Optica) are
  primary -- cite them when the claim rests on the paper. Use the DOI or
  the arxiv abstract URL.
- Yahoo Finance quote pages, Seeking Alpha, FinancialContent, Benzinga,
  TipRanks, Simply Wall St, market-report SEO mills (MarketsAndMarkets,
  Grand View Research, Mordor, Allied) and press-release wires
  (PRNewswire, BusinessWire, GlobeNewswire) are secondary. Use them only
  when no primary source exists -- and prefer linking the issuer's own
  IR page over the wire's mirror of the same release.

A report whose citations are <30% primary fails the audit gate and is
sent back to research with this brief attached."""


def primary_source_only_retry_brief(
    *, theme: str, observed_share: float, n_primary: int, n_total: int,
    threshold: float = PRIMARY_SOURCE_MIN_SHARE,
) -> str:
    """Build the retry brief the audit gate hands back to research when the
    primary-source share is below the floor. Concrete, theme-aware, and
    citation-rule-first -- the analyst's job on the second pass is to find
    a primary URL for every measurement / filing / product claim that's
    currently riding on a secondary aggregator, or kill the claim."""
    pct = observed_share * 100
    floor_pct = threshold * 100
    return f"""\
PRIMARY-SOURCE-ONLY RETRY -- THIS REPORT FAILED THE AUDIT GATE.

THEME: {theme}

The first research pass produced {n_primary}/{n_total} primary-source
citations ({pct:.0f}%). The firm requires at least {floor_pct:.0f}%.
Re-run your research with this single objective: replace every secondary
citation with a primary one, or strike the underlying claim.

{CITATION_DISCIPLINE_DIRECTIVE}

Concrete moves to make on this pass:

1. For every "as reported" / "per Reuters" / "per Yahoo Finance" claim in
   your prior notes -- find the original filing, press release, or data
   series. If you can't locate it, drop the claim. Do not paraphrase a
   secondary source and call it primary.
2. Vendor / product claims (chip launches, drug approvals, deal terms):
   pull the company's own IR press release or the relevant 8-K. Link
   the IR URL or the EDGAR document URL.
3. Standards / measurement claims (IEEE 802.3, OFC, IETF, 3GPP): link the
   working group meeting page, the paper on ieeexplore.ieee.org, or the
   OFC session/paper number on ofcconference.org / opg.optica.org. Cite
   the paper title and date.
4. Regulator / macro claims: link the regulator's deep page (FRED series,
   SEC filing, EIA series, BLS table) -- never the homepage, never an
   aggregator's mirror.
5. If a research paper supports the claim, cite it. arxiv abstract URL
   or DOI is fine.

Output the same notes shape as before -- structured markdown with inline
`[anchor](url)` citations -- but with the primary-source rule enforced.
The audit gate will recompute the share once you're done."""


# Marker filename written into a report's working dir when the audit stage
# routes back to research. The research stage reads this file (if present)
# and passes its contents as the retry_brief argument to Analyst.research().
PRIMARY_SOURCE_RETRY_FILENAME: str = "primary-source-retry-brief.md"


class Scout(Agent):
    role = "scout"
    default_model = settings.model_haiku

    def __init__(self, cost: CostTracker, **kwargs: object) -> None:
        super().__init__(
            persona_path=settings.team_dir / "scout.md",
            cost=cost,
            **kwargs,  # type: ignore[arg-type]
        )

    def run_daily_digest(
        self,
        recent_headlines: list[str] | None = None,
        recent_report_themes: list[str] | None = None,
    ) -> AgentResult:
        prior = "\n".join(f"- {h}" for h in (recent_headlines or [])[:30])
        subs = ", ".join(DEFAULT_SUBREDDITS)
        # Theme graph: tags from the firm's recent published reports. The
        # Scout uses these to weight natural follow-ups higher than cold
        # themes -- the connective tissue between yesterday's report and
        # today's pitch.
        follow_up_blob = (
            "\n".join(f"- {t}" for t in recent_report_themes[:20])
            if recent_report_themes else "(none -- no prior reports tagged yet)"
        )
        prompt = f"""You are running the daily morning scan for Forte Research. Surface the 10 most interesting themes for today's note: things that are moving, things the buyside is talking about, things that are underpriced or just-starting narratives.

You have three scan tools:
- `web_search` (Anthropic-managed) -- broad news, broker notes, primary sources.
- `reddit_hot` -- sentiment + what's actually being discussed. Suggested subs: {subs}.
- `hn_search` -- tech-adjacent themes (semis, AI, crypto, biotech, regulation).

Use a mix. Don't lean only on web_search.

THE FIRM'S RECENT WORK (covered in the past 8 reports):
{follow_up_blob}

When a candidate theme is a natural follow-up to one of the above (a second-order effect, a regime change in the same trade, a name you'd buy if a previous thesis is right) WEIGHT IT HIGHER. Connective tissue across the firm's reports is what makes Forte feel like a research firm, not a daily-digest mill. Don't force it -- but if a candidate genuinely extends past work, give it priority over a cold start.

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
