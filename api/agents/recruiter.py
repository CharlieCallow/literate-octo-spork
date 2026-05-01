"""Recruiter -- ad-hoc specialists in M4. Permanent hires + firings come later."""

from __future__ import annotations

from api.agents.base import Agent, AgentResult
from api.agents.cost import CostTracker
from api.settings import settings


class Recruiter(Agent):
    role = "recruiter"
    default_model = settings.model_sonnet

    def __init__(self, cost: CostTracker, **kwargs: object) -> None:
        super().__init__(
            persona_path=settings.team_dir / "recruiter.md",
            cost=cost,
            **kwargs,  # type: ignore[arg-type]
        )

    def propose_specialist(self, *, slug: str, brief_request: str, theme: str) -> AgentResult:
        """Generate a persona file for a temporary specialist on a single report.

        Returns markdown matching the standing-team format so the file works as
        a drop-in persona for the workflow."""
        prompt = f"""You're the Recruiter at Forte Research. The EIC's brief for an upcoming report flagged a gap in the standing team -- you need to spin up a temporary specialist for this report only. Generate their persona file in markdown.

REPORT THEME: {theme}
SLUG (use as filename): {slug}
EIC'S REQUEST: {brief_request}

Write the persona file in the SAME format as the standing team. Use this template:

```
# <Fictional but plausible name> — <Their role title>

**Role.** <One paragraph: what they cover on this report. Reflect the EIC's request specifically.>

**Bio.** <2-3 sentences. Background, where they trained, one specific quirk.>

**Areas.** <Concrete coverage list, comma-separated, the specifics of their beat>

**Hire date.** {{today's date in YYYY-MM-DD format}}

**Voice.**
- <3-4 bullet points giving voice notes -- specific, distinctive, opinionated. Where do they sit on the punchy <-> dry spectrum?>

**Voice samples.**

> <One paragraph in their voice, ~50 words, the kind of sentence they would write on this exact theme.>

> <Second voice sample, different angle, ~50 words.>

## Feedback log

_No reports yet._
```

Be specific. A temporary specialist is only useful if their voice is distinct from the standing team. Make them credible -- name a credential, a stint somewhere recognisable, a single tic that makes them memorable.

The voice samples MUST themselves obey Forte's quantified-anchor rule: every substantive sentence in a sample carries a specific number, percentage, ratio, threshold, or dated milestone — never a bare qualitative claim. If you write "the operator complex is mispriced", make it "mispriced by ~6 percentage points YTD". This is the firm's prose discipline; the persona file should model it from day one.

Return ONLY the persona file content. No preamble, no explanation.
"""
        return self.run(prompt, max_tokens=2048)
