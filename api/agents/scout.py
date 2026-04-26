"""Scout — daily theme digest. Stub for M1, wired in M3."""

from __future__ import annotations

from api.agents.base import Agent
from api.agents.cost import CostTracker
from api.settings import settings


class Scout(Agent):
    role = "scout"
    default_model = settings.model_haiku

    def __init__(self, cost: CostTracker, **kwargs: object) -> None:
        super().__init__(
            persona_path=settings.team_dir / "scout.md",
            cost=cost,
            **kwargs,  # type: ignore[arg-type]
        )

    # The daily-digest method lands in M3.
