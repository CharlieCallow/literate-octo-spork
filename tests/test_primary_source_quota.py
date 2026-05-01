"""Primary-source classifier + quota retry brief tests.

Covers the audit-gate machinery added in Ticket 5: the is_primary_source()
classifier, the primary_source_share() rollup the audit stage uses, and the
retry-brief builder the gate hands back to the research stage."""

from __future__ import annotations

from api.agents.scout import (
    CITATION_DISCIPLINE_DIRECTIVE,
    PRIMARY_SOURCE_MIN_SHARE,
    primary_source_only_retry_brief,
)
from api.citations import is_primary_source, primary_source_share


def test_is_primary_recognises_regulators_and_filings() -> None:
    assert is_primary_source("https://www.sec.gov/Archives/edgar/data/0/x.htm")
    assert is_primary_source("https://fred.stlouisfed.org/series/DGS10")
    assert is_primary_source("https://www.federalreserve.gov/monetarypolicy.htm")
    assert is_primary_source("https://www.fda.gov/drugs/foo")
    assert is_primary_source("https://clinicaltrials.gov/study/NCT01")


def test_is_primary_recognises_standards_and_papers() -> None:
    assert is_primary_source("https://ieeexplore.ieee.org/document/12345")
    assert is_primary_source("https://standards.ieee.org/ieee/802.3/")
    assert is_primary_source("https://opg.optica.org/abstract.cfm?uri=OFC-2026-Tu1A.1")
    assert is_primary_source("https://arxiv.org/abs/2401.00001")
    assert is_primary_source("https://www.nature.com/articles/foo")


def test_is_primary_recognises_company_ir_press_releases() -> None:
    assert is_primary_source("https://investors.broadcom.com/news-releases/foo")
    assert is_primary_source("https://ir.cisco.com/press-release/2026/bailly")
    assert is_primary_source("https://www.example-corp.com/investors/press-release/x")


def test_is_primary_rejects_secondary_aggregators() -> None:
    assert not is_primary_source("https://finance.yahoo.com/quote/BRCM")
    assert not is_primary_source("https://seekingalpha.com/article/foo")
    assert not is_primary_source("https://www.financialcontent.com/page")
    assert not is_primary_source("https://www.marketsandmarkets.com/Market-Reports/x")
    assert not is_primary_source("https://www.prnewswire.com/news-releases/foo")
    assert not is_primary_source(None)
    assert not is_primary_source("")


def test_primary_source_share_rollup() -> None:
    sources = [
        {"url": "https://www.sec.gov/Archives/x"},
        {"url": "https://fred.stlouisfed.org/series/DGS10"},
        {"url": "https://finance.yahoo.com/quote/AAPL"},
        {"url": "https://seekingalpha.com/article/foo"},
        {"url": None},
    ]
    n_primary, n_total, share = primary_source_share(sources)
    assert n_primary == 2
    assert n_total == 4
    assert share == 0.5


def test_primary_source_share_empty() -> None:
    assert primary_source_share([]) == (0, 0, 0.0)


def test_retry_brief_includes_directive_and_observed_share() -> None:
    brief = primary_source_only_retry_brief(
        theme="silicon photonics in the optics build-out",
        observed_share=0.10,
        n_primary=1,
        n_total=10,
    )
    assert "silicon photonics" in brief
    assert "10%" in brief  # observed share
    assert f"{int(PRIMARY_SOURCE_MIN_SHARE * 100)}%" in brief
    # The directive itself is spliced in so the retry brief is self-contained.
    assert CITATION_DISCIPLINE_DIRECTIVE in brief
