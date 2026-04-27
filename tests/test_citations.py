"""Citation extraction tests. Tools that return URL-like fields should yield
Citations; non-URL outputs should be a no-op."""

from __future__ import annotations

import json
from types import SimpleNamespace

from api.agents.base import (
    Citation,
    _extract_local_citations,
    _extract_server_citations,
)


def test_local_extracts_url_from_wikipedia_payload() -> None:
    out = json.dumps({
        "title": "GLP-1",
        "extract": "...",
        "url": "https://en.wikipedia.org/wiki/Glucagon-like_peptide-1",
    })
    cs: list[Citation] = []
    _extract_local_citations("wikipedia_summary", out, cs)
    assert len(cs) == 1
    assert cs[0].url.startswith("https://en.wikipedia.org/")
    assert cs[0].source == "wikipedia_summary"


def test_local_extracts_each_filing_url() -> None:
    out = json.dumps({
        "ticker": "NVDA",
        "filings": [
            {"form": "10-K", "url": "https://sec.gov/foo"},
            {"form": "10-Q", "url": "https://sec.gov/bar"},
        ],
    })
    cs: list[Citation] = []
    _extract_local_citations("edgar_filings", out, cs)
    assert [c.url for c in cs] == ["https://sec.gov/foo", "https://sec.gov/bar"]
    assert all(c.source == "edgar_filings" for c in cs)


def test_local_no_op_on_non_json() -> None:
    cs: list[Citation] = []
    _extract_local_citations("fred_series", "not json", cs)
    assert cs == []


def test_local_no_op_when_no_url_key() -> None:
    cs: list[Citation] = []
    _extract_local_citations("fred_series", json.dumps({"series_id": "DGS10"}), cs)
    assert cs == []


def test_local_extracts_reddit_permalinks() -> None:
    out = json.dumps({
        "subreddit": "wallstreetbets",
        "posts": [
            {"title": "NVDA earnings", "permalink": "https://reddit.com/r/wsb/abc"},
            {"title": "Macro thread", "permalink": "https://reddit.com/r/wsb/def"},
        ],
    })
    cs: list[Citation] = []
    _extract_local_citations("reddit_hot", out, cs)
    assert [c.url for c in cs] == ["https://reddit.com/r/wsb/abc", "https://reddit.com/r/wsb/def"]


def test_local_extracts_hn_story_urls() -> None:
    out = json.dumps({
        "query": "semiconductors",
        "stories": [
            {"title": "TSMC outlook", "url": "https://example.com/x", "hn_url": "https://news.ycombinator.com/1"},
            {"title": "Discussion only", "hn_url": "https://news.ycombinator.com/2"},  # no external url
        ],
    })
    cs: list[Citation] = []
    _extract_local_citations("hn_search", out, cs)
    # First story gives the external URL; second falls back to the HN discussion link.
    assert [c.url for c in cs] == ["https://example.com/x", "https://news.ycombinator.com/2"]


def test_server_extracts_web_search_results() -> None:
    # Mimic the Anthropic SDK's content blocks — duck-typed objects with
    # `type` and `content`/`url` attributes.
    block = SimpleNamespace(
        type="web_search_tool_result",
        content=[
            SimpleNamespace(url="https://ft.com/x", title="FT piece"),
            SimpleNamespace(url="https://bloomberg.com/y", title="BB piece"),
        ],
    )
    cs: list[Citation] = []
    _extract_server_citations([block], cs)
    assert [c.url for c in cs] == ["https://ft.com/x", "https://bloomberg.com/y"]
    assert all(c.source == "web" for c in cs)


def test_server_skips_non_web_blocks() -> None:
    block = SimpleNamespace(type="text", text="hello")
    cs: list[Citation] = []
    _extract_server_citations([block], cs)
    assert cs == []
