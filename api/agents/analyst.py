"""Generic analyst. Macro / equity flavours selected by persona file at runtime."""

from __future__ import annotations

from pathlib import Path

from api.agents.base import Agent, AgentResult, Tool
from api.agents.cost import CostTracker
from api.agents.tools import (
    arxiv_tool,
    clinical_trials_tool,
    coingecko_markets_tool,
    coingecko_trending_tool,
    defillama_tool,
    edgar_filings_tool,
    eia_series_tool,
    fred_series_tool,
    gdelt_tool,
    github_repo_tool,
    github_search_tool,
    openfda_labels_tool,
    openfda_recalls_tool,
    uploaded_documents_tool,
    web_search_tool,
    wikipedia_tool,
    worldbank_tool,
    yfinance_history_tool,
)
from api.models import ReportMode
from api.settings import settings


class Analyst(Agent):
    role = "analyst"
    default_model = settings.model_sonnet

    def __init__(
        self,
        persona_filename: str,
        cost: CostTracker,
        **kwargs: object,
    ) -> None:
        super().__init__(
            persona_path=settings.team_dir / persona_filename,
            cost=cost,
            **kwargs,  # type: ignore[arg-type]
        )

    def research(
        self,
        brief: str,
        theme: str,
        working_dir: Path,
        *,
        mode: ReportMode = ReportMode.standard,
        report_id: int | None = None,
        has_uploads: bool = False,
    ) -> AgentResult:
        upload_note = (
            "\n\nThe user attached research notes / CSVs to this report. "
            "Call `uploaded_documents` with no args first to see what's there, "
            "then pass `filename` to read each one. Treat these as primary "
            "sources -- they reflect the user's own thinking and data.\n"
            if has_uploads else ""
        )
        prompt = f"""You're a contributing analyst on a Forte Research report. The Editor-in-Chief's brief is below.

BRIEF:

{brief}

THEME: {theme}{upload_note}

Run your research. Available tools:

Markets / macro:
- `fred_series` — US macro time series (rates, CPI, employment, etc.)
- `yfinance_history` — equity / ETF / FX / crypto / futures price history
- `worldbank_series` — cross-country macro (GDP, debt, FX reserves)
- `eia_series` — US energy data (oil, gas, electricity)

Crypto / DeFi:
- `coingecko_markets` — top crypto coins by market cap with % changes
- `coingecko_trending` — what's trending on CoinGecko
- `defillama` — DeFi TVL by protocol or by chain

Filings / regulatory:
- `edgar_filings` — recent SEC filings for a ticker (10-K, 10-Q, 8-K)
- `openfda_drug_labels` — FDA drug labels search
- `openfda_recalls` — recent drug recalls
- `clinical_trials` — ClinicalTrials.gov pipeline data (sponsor, phase, status)

Research / sentiment:
- `arxiv_search` — research papers (AI / quant / biotech / physics)
- `github_repo` / `github_search` — developer activity for tech themes
- `gdelt_news` — global news search (broader than web_search)
- `wikipedia_summary` — definitional and background context
- `web_search` — current news, broker notes (Anthropic-managed)

Use whichever tools fit your beat. Stay in your voice. Write structured notes in markdown -- claims with evidence and sources. Cite inline: when a claim rests on a specific source, link it like `[short anchor text](https://exact-url)` so the renderer can turn it into a numbered footnote. Use real URLs from your tool results, never invent them. Be opinionated; hedging without conviction is the failure mode. Output ~300-500 words.
"""
        tools: list[Tool] = [
            fred_series_tool(),
            yfinance_history_tool(),
            wikipedia_tool(),
            edgar_filings_tool(),
            coingecko_markets_tool(),
            coingecko_trending_tool(),
            eia_series_tool(),
            clinical_trials_tool(),
            github_repo_tool(),
            github_search_tool(),
            arxiv_tool(),
            gdelt_tool(),
            worldbank_tool(),
            openfda_labels_tool(),
            openfda_recalls_tool(),
            defillama_tool(),
        ]
        if has_uploads and report_id is not None:
            tools.append(uploaded_documents_tool(report_id))
        # Per-mode tuning. fast: drop web search (biggest cost driver) and
        # tighten loop. deep: bigger token + iter budget for thorough research.
        server_tools = [] if mode == ReportMode.fast else [web_search_tool()]
        max_iters = {ReportMode.fast: 4, ReportMode.standard: 6, ReportMode.deep: 10}[mode]
        max_tokens = {ReportMode.fast: 2048, ReportMode.standard: 3072, ReportMode.deep: 4096}[mode]
        return self.run(
            prompt,
            tools=tools,
            server_tools=server_tools,
            max_tokens=max_tokens,
            max_iters=max_iters,
        )

    def draft(self, brief: str, notes: str, theme: str) -> AgentResult:
        prompt = f"""Draft your section of the report based on the notes below. Stay in your voice (the persona file is your identity). The Editor will preserve voice when editing -- write in the voice you actually want to read.

BRIEF:

{brief}

YOUR NOTES:

{notes}

Output a single section in markdown. Start with a brief inline header (## <Section heading>). 200-400 words. Reference charts inline as `[chart: <filename>]` if you want one rendered (Data & Charts will produce them). Cite real sources inline using markdown links `[anchor text](https://url)` -- the renderer turns these into numbered footnotes. Do not invent data or URLs -- only use figures and links from your notes.
"""
        return self.run(prompt, max_tokens=2048)
