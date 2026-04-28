"""Budget tracker tests. Hard caps must halt; warns are advisory."""

from __future__ import annotations

import pytest

from api.agents.cost import BudgetExceeded, CostTracker, Usage, cost_for


def test_haiku_pricing_per_million() -> None:
    # Haiku 4.5: $1/M input, $5/M output (placeholder, see cost.py)
    spend = cost_for("claude-haiku-4-5-20251001", Usage(input_tokens=1_000_000, output_tokens=1_000_000))
    assert spend == pytest.approx(6.0)


def test_unknown_model_falls_back_to_sonnet_pricing() -> None:
    spend = cost_for("claude-mystery-99", Usage(input_tokens=1_000_000, output_tokens=0))
    assert spend == pytest.approx(3.0)  # Sonnet input price


def test_report_cap_hits() -> None:
    t = CostTracker(report_cap=0.05, day_cap=10.0)
    # 1M output tokens of Sonnet = $15 — well over $0.05 cap
    with pytest.raises(BudgetExceeded, match="Per-report cap"):
        t.add("claude-sonnet-4-6", Usage(input_tokens=0, output_tokens=1_000_000))


def test_day_cap_hits_with_carry_over() -> None:
    t = CostTracker(report_cap=10.0, day_cap=1.0, day_spent=0.95)
    with pytest.raises(BudgetExceeded, match="Per-day cap"):
        t.add("claude-haiku-4-5-20251001", Usage(input_tokens=200_000, output_tokens=0))


def test_under_caps_returns_spend() -> None:
    t = CostTracker(report_cap=10.0, day_cap=10.0)
    spend = t.add("claude-haiku-4-5-20251001", Usage(input_tokens=10_000, output_tokens=10_000))
    assert spend == pytest.approx(0.06)  # 10k * $1/M + 10k * $5/M
    assert t.report_spent == spend
    assert t.day_spent == spend


def test_cache_read_billed_at_one_tenth_of_input() -> None:
    # Cache hits cost 10% of base input price, so 1M cache-read Sonnet tokens
    # ($3/M base) = $0.30, not $3.00.
    spend = cost_for(
        "claude-sonnet-4-6",
        Usage(input_tokens=0, output_tokens=0, cache_read_tokens=1_000_000),
    )
    assert spend == pytest.approx(0.30)


def test_cache_creation_billed_at_125_percent_of_input() -> None:
    # 5-minute cache writes cost 1.25x base input. 1M Sonnet creation tokens
    # = $3.75.
    spend = cost_for(
        "claude-sonnet-4-6",
        Usage(input_tokens=0, output_tokens=0, cache_creation_tokens=1_000_000),
    )
    assert spend == pytest.approx(3.75)
