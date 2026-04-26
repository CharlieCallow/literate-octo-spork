"""Per-call cost tracking. Prices are approximate USD per million tokens.
Update against the public Anthropic price list when it changes."""

from __future__ import annotations

from dataclasses import dataclass

# USD per million tokens. (input, output)
_PRICING: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-opus-4-7": (15.00, 75.00),
}


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0


def cost_for(model: str, usage: Usage) -> float:
    in_price, out_price = _PRICING.get(model, (3.00, 15.00))
    return (
        (usage.input_tokens + usage.cache_read_tokens) * in_price
        + usage.output_tokens * out_price
    ) / 1_000_000


class BudgetExceeded(RuntimeError):
    """Raised when a per-report or per-day cost cap is hit."""


class CostTracker:
    def __init__(self, *, report_cap: float, day_cap: float, day_spent: float = 0.0) -> None:
        self.report_cap = report_cap
        self.day_cap = day_cap
        self.report_spent = 0.0
        self.day_spent = day_spent

    def add(self, model: str, usage: Usage) -> float:
        spend = cost_for(model, usage)
        self.report_spent += spend
        self.day_spent += spend
        if self.report_spent > self.report_cap:
            raise BudgetExceeded(f"Per-report cap ${self.report_cap:.2f} exceeded (${self.report_spent:.2f})")
        if self.day_spent > self.day_cap:
            raise BudgetExceeded(f"Per-day cap ${self.day_cap:.2f} exceeded (${self.day_spent:.2f})")
        return spend
