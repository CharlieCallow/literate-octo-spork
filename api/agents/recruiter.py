"""Recruiter — ad-hoc and permanent hires, fire recommendations. Stub for M1, wired in M4."""

from __future__ import annotations

from api.agents.base import Agent
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

    # Hire/fire methods land in M4.
