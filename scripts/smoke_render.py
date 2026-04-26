"""Render a sample PDF + chart with stub content. No Anthropic / FRED calls.

Run:  python -m scripts.smoke_render
Output: ./smoke-out/report.pdf, ./smoke-out/chart.png
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from api.render.charts import line_chart
from api.render.pdf import Contributor, Section, render_pdf

OUT = Path("smoke-out")


def main() -> None:
    OUT.mkdir(exist_ok=True)

    idx = pd.date_range("2020-01-01", periods=60, freq="ME")
    chart_df = pd.DataFrame({"10Y yield (synthetic)": pd.Series(range(60)).values * 0.05 + 1.0}, index=idx)
    chart = line_chart(
        chart_df,
        title="10Y Treasury yield, 2020-2024 (synthetic)",
        subtitle="The duration trade is no longer free.",
        source="FRED (synthetic)",
        as_of="2024-12-01",
        out_path=OUT / "chart.png",
    )

    render_pdf(
        out_path=OUT / "report.pdf",
        title="The Nuclear Renaissance, Sized",
        subtitle="A first look at the supply, demand, and political reality",
        date=date.today().isoformat(),
        contributors=[
            Contributor("Margaux Devlin", "Editor-in-Chief"),
            Contributor("Henrik Voss", "Macro Strategist"),
            Contributor("Tomás Reyes", "Data & Charts"),
        ],
        sections=[
            Section(
                heading="Opening",
                body_md=(
                    "Markets keep telling you nuclear is back. Markets are right; the framing is wrong. "
                    "This is not a renaissance — it is a permission slip, and the permission has not been priced."
                ),
            ),
            Section(
                heading="Macro view",
                body_md=(
                    "**The base case:** uranium tightens through 2027.\n\n"
                    "The Western fuel cycle is short of conversion and enrichment, not pounds. "
                    "The pounds story is the easy one to tell; the bottleneck story is the actual trade."
                ),
                author="Henrik Voss",
                role="Macro Strategist",
            ),
            Section(
                heading="Data & charts",
                body_md=(
                    f'<figure><img src="{(OUT / "chart.png").resolve().as_uri()}" alt="rates"></figure>\n\n'
                    "Spot uranium up 220% off the 2020 lows. Term contracts catching up. The gap closes."
                ),
                author="Tomás Reyes",
                role="Data & Charts",
            ),
            Section(
                heading="Closing",
                body_md="The way to be wrong here is to assume the political consensus holds. It might not.",
            ),
        ],
        house_view_top=(
            "Long the bottleneck, not the pounds. Conversion and enrichment are where the next 18 months of P&L lives."
        ),
        house_view_bottom="Buy the fuel cycle, sell the headline.",
        read_minutes=8,
    )

    print(f"chart -> {chart}")
    print(f"pdf   -> {OUT / 'report.pdf'}")


if __name__ == "__main__":
    main()
