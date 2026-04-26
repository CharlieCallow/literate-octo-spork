"""Editor-in-Chief. Opus-tier. Briefs at the start, edits at the end."""

from __future__ import annotations

from pathlib import Path

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

    def write_brief(self, theme: str, *, subtitle: str | None = None) -> AgentResult:
        prompt = f"""A new theme has been commissioned for a Forte Research report.

THEME: {theme}
SUBTITLE: {subtitle or "(none yet — propose one)"}

Write a one-page brief in markdown. Cover:
- The angle (one sentence — what the report's actually arguing).
- The 3-5 key questions the report must answer.
- Proposed structure (3-5 sections with one-line descriptions and which kind of analyst writes each).
- One or two visualisations you want to see.
- Any data sources to prioritise.

Be opinionated. This is the brief that the team works from.
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
