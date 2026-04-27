"""Editor-in-Chief. Opus-tier. Briefs at the start, edits at the end."""

from __future__ import annotations

from api.agents.base import Agent, AgentResult
from api.agents.cost import CostTracker
from api.settings import settings


class EditorInChief(Agent):
    role = "editor-in-chief"
    default_model = settings.model_opus

    def __init__(self, cost: CostTracker, **kwargs: object) -> None:
        super().__init__(
            persona_path=settings.team_dir / "editor-in-chief.md",
            cost=cost,
            **kwargs,  # type: ignore[arg-type]
        )

    def write_brief(
        self,
        theme: str,
        *,
        subtitle: str | None = None,
        available_contributors: list[dict[str, str]] | None = None,
    ) -> AgentResult:
        roster = available_contributors or []
        roster_blob = "\n".join(f"- `{c['slug']}` — {c['name']} ({c['role']})" for c in roster) or "(none)"

        prompt = f"""A new theme has been commissioned for a Forte Research report. Write a structured brief.

THEME: {theme}
SUBTITLE: {subtitle or "(propose one)"}

AVAILABLE CONTRIBUTORS:
{roster_blob}

DATA SOURCES THE TEAM CAN PULL FROM:
- FRED -- macro: rates, CPI, unemployment, GDP, money supply, etc.
- yfinance -- equities, ETFs, FX, commodity futures, crypto. Tickers like 'AAPL', 'SPY', '^VIX', 'BTC-USD', 'CL=F'.
- SEC EDGAR -- 10-K / 10-Q / 8-K filings for any US-listed ticker.
- Wikipedia -- definitional and background content.
- Web search -- current news, headlines, broker notes.

Output the brief in markdown using EXACTLY these literal section headings (they're parsed by the workflow):

# ANGLE
<one sentence — what this report is actually arguing>

# SUBTITLE
<one sentence — short, descriptive, goes on the cover page under the title>

# QUESTIONS
1. <question 1>
2. <question 2>
3. <question 3>

# CONTRIBUTORS
<one bullet per contributor we want on this report. Use the slug verbatim from AVAILABLE CONTRIBUTORS. Format:>
- `<slug>`: <one line on what they cover for this report>

# CHARTS
<2-3 chart ideas, each one bullet:>
- <chart title> — <what it shows / which data source / why it lands>

# DATA SOURCES
- <source>: <series IDs / tickers / queries>

# STRUCTURE
<3-5 sections in the order they should appear, each one bullet describing the section topic.>

Be opinionated. This is the brief the team works from.
"""
        return self.run(prompt, max_tokens=2048)

    def edit(
        self,
        *,
        brief: str,
        sections: list[dict[str, str]],
        chart_summary: str,
    ) -> AgentResult:
        sec_blob = "\n\n---\n\n".join(
            f"## SECTION ({s['author']} — {s.get('role','analyst')}): {s['heading']}\n\n{s['body']}"
            for s in sections
        )
        prompt = f"""You are editing a draft Forte Research report. Your job: tighten, kill weak claims, write the opening and the bottom-line. PRESERVE EACH SECTION'S VOICE — homogenising into a house voice is the failure mode. The brief is below for reference, then the analyst sections, then a summary of charts.

BRIEF:

{brief}

SECTIONS:

{sec_blob}

CHARTS:

{chart_summary}

Return JSON-ish markdown in EXACTLY this structure (use the literal headings — they're parsed):

# OPENING
<2-3 punchy paragraphs that frame the whole report. Your voice. End with a one-line thesis.>

# HOUSE VIEW (TOP)
<one short sentence — the headline take, surfaced at the top of the PDF in a navy callout.>

# REVISED SECTIONS
<For each input section, output exactly:
## <heading>
**author:** <author name>
**role:** <role>

<edited body. Keep their voice. Cut hedge-words. Demand evidence stays. ~200-400 words each.>>

# HOUSE VIEW (BOTTOM)
<one short sentence — the bottom-line takeaway, navy callout at the end of the report.>

# CLOSING
<one short paragraph — what to watch, where you'll be wrong, when to revisit.>
"""
        return self.run(prompt, max_tokens=4096)
