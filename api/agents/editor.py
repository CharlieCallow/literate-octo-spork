"""Editor-in-Chief. Opus-tier. Briefs at the start, edits at the end, writes feedback."""

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

    def write_feedback(
        self,
        *,
        contributor_name: str,
        contributor_role: str,
        theme: str,
        original_draft: str,
        edited_section: str,
    ) -> AgentResult:
        prompt = f"""You're the Editor-in-Chief writing post-report feedback on a contributor's work. The note will be appended to their persona file's '## Feedback log' section -- they read it before the next assignment.

CONTRIBUTOR: {contributor_name} -- {contributor_role}
REPORT THEME: {theme}

THEIR ORIGINAL DRAFT:

{original_draft}

YOUR EDITED VERSION:

{edited_section}

Write a single short feedback note (60-120 words). Cover:
- What worked in their voice or take
- What you rewrote and why (be specific -- name a sentence or claim)
- One concrete thing for next time

Direct, not nice. No headings, no list -- just a paragraph. Stay in your voice.
"""
        return self.run(prompt, max_tokens=512)

    def write_brief(
        self,
        theme: str,
        *,
        subtitle: str | None = None,
        available_contributors: list[dict[str, str]] | None = None,
        past_reports: list[dict[str, str]] | None = None,
    ) -> AgentResult:
        roster = available_contributors or []
        roster_blob = "\n".join(f"- `{c['slug']}` — {c['name']} ({c['role']})" for c in roster) or "(none)"

        if past_reports:
            past_blob = "\n".join(
                f"- {r['theme']}" + (f" -- {r['subtitle']}" if r.get('subtitle') else "")
                for r in past_reports
            )
        else:
            past_blob = "(none -- this is the firm's first published report)"

        prompt = f"""A new theme has been commissioned for a Forte Research report. Write a structured brief.

THEME: {theme}
SUBTITLE: {subtitle or "(propose one)"}

PRIOR PUBLISHED REPORTS (most recent first):
{past_blob}

Do NOT reference past reports that aren't on the list above. If the list is empty, this really is the first report -- don't pretend the firm has prior history. Anchor only to claims you can verify with tool calls or that appear in the past list.

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

# AD-HOC SPECIALIST
<Optional. Only include this section if the standing roster genuinely lacks the expertise this theme needs (e.g. clinical-trial reads for a biotech theme). Each bullet:>
- `<short-slug-with-dashes>`: <one line on what they cover and why standing analysts fall short>
<If you don't need a specialist, write "(none)" or omit the section.>

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
