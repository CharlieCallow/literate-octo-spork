"""Render a sample multi-author PDF + 3 charts with stub content. No API calls.

Run:  python -m scripts.smoke_render
Output: ./smoke-out/report.pdf, ./smoke-out/charts/*.png
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from api.render.charts import bar_chart, line_chart
from api.render.pdf import Contributor, Section, render_pdf

OUT = Path("smoke-out")
CHARTS = OUT / "charts"


def _build_charts() -> dict[str, Path]:
    CHARTS.mkdir(parents=True, exist_ok=True)
    idx = pd.date_range("2020-01-01", periods=72, freq="ME")

    rates_df = pd.DataFrame(
        {"10Y": np.linspace(1.0, 4.2, 72), "2Y": np.linspace(0.4, 4.7, 72)},
        index=idx,
    )
    rates = line_chart(
        rates_df,
        title="2Y / 10Y Treasury yields, 2020-2025 (synthetic)",
        subtitle="The curve un-inverted in late 2024 and never looked back.",
        source="FRED (synthetic)",
        as_of=date.today().isoformat(),
        out_path=CHARTS / "rates.png",
    )

    cpi_df = pd.DataFrame(
        {"CPI YoY (%)": 2.0 + 7.0 * np.exp(-np.linspace(0, 4, 72)) + np.random.normal(0, 0.1, 72)},
        index=idx,
    )
    cpi = line_chart(
        cpi_df,
        title="Headline CPI, year-over-year (synthetic)",
        subtitle="Inflation cooled, but the disinflation tail is now flat.",
        source="FRED (synthetic)",
        as_of=date.today().isoformat(),
        out_path=CHARTS / "cpi.png",
    )

    sector = pd.Series(
        [12.4, 9.1, -3.2, 18.7, -1.4, 6.0],
        index=["Tech", "Energy", "Utilities", "Financials", "Staples", "Industrials"],
        name="YTD return (%)",
    )
    sectors = bar_chart(
        sector,
        title="S&P 500 sector returns, year-to-date (synthetic)",
        subtitle="Dispersion is wider than it looks at the index level.",
        source="yfinance (synthetic)",
        as_of=date.today().isoformat(),
        out_path=CHARTS / "sectors.png",
    )

    return {"rates": rates, "cpi": cpi, "sectors": sectors}


def main() -> None:
    OUT.mkdir(exist_ok=True)
    charts = _build_charts()

    def fig(p: Path, alt: str) -> str:
        return f'<figure><img src="{p.resolve().as_uri()}" alt="{alt}"></figure>'

    render_pdf(
        out_path=OUT / "report.pdf",
        title="The Dispersion Trade, Re-Examined",
        subtitle="Why the index-level calm hides a wider security-level spread",
        date=date.today().isoformat(),
        contributors=[
            Contributor("Margaux Devlin", "Editor-in-Chief"),
            Contributor("Henrik Voss", "Macro Strategist"),
            Contributor("Priya Anand", "Equity / Sector Analyst"),
            Contributor("Tomás Reyes", "Data & Charts"),
        ],
        sections=[
            Section(
                heading="Opening",
                body_md=(
                    "Index volatility is at multi-year lows. Single-stock dispersion is not. "
                    "If you are still trading the index, you are trading the average — and the "
                    "average has stopped being interesting.\n\nThe thesis: own the dispersion."
                ),
            ),
            Section(
                heading="Macro view",
                body_md=(
                    "**The base case:** the curve is finished doing the heavy lifting.\n\n"
                    "Two-year yields are anchored, ten-year yields are range-bound, the policy rate "
                    "has been the binding constraint for eighteen months and the next move is a cut, "
                    "not a hike. The duration trade is over because the duration repricing is over."
                ),
                author="Henrik Voss",
                role="Macro Strategist",
            ),
            Section(
                heading="Equity exposure",
                body_md=(
                    "Sector returns YTD tell the story the index won't. Financials +18.7%, "
                    "Utilities -3.2%. That spread is not noise; it's the new factor.\n\n"
                    "**Where to be long:** beta-to-rates names that the market is treating as "
                    "rate-insensitive. **Where to be short:** the bond proxies trading like 2019."
                ),
                author="Priya Anand",
                role="Equity / Sector Analyst",
            ),
            Section(
                heading="Data & charts",
                body_md=(
                    f"{fig(charts['rates'], 'rates')}\n\n"
                    "Curve un-inversion held. Two-year yields anchored 100 bps below the policy rate "
                    "for nine months and counting.\n\n"
                    f"{fig(charts['cpi'], 'cpi')}\n\n"
                    "Disinflation tail is flat. The last 80 bps of CPI is doing what it always does — "
                    "stubbornly nothing.\n\n"
                    f"{fig(charts['sectors'], 'sectors')}\n\n"
                    "Sector dispersion at 22 percentage points YTD. That's a real number."
                ),
                author="Tomás Reyes",
                role="Data & Charts",
            ),
            Section(
                heading="Closing",
                body_md=(
                    "The way to be wrong here is to assume the dispersion regime persists. It might not. "
                    "Watch the realised-vs-implied vol gap on the index — that's the canary."
                ),
            ),
        ],
        house_view_top=(
            "Trade the dispersion, not the index. Beta-to-rates is the cleanest single factor in equities right now."
        ),
        house_view_bottom="Long dispersion, short the average.",
        read_minutes=10,
    )

    print(f"charts -> {CHARTS}")
    print(f"pdf    -> {OUT / 'report.pdf'}")


if __name__ == "__main__":
    main()
