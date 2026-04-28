"""Tests for the agent-output sanitizer that strips Anthropic web_search
citation markup leaking into agent text."""

from __future__ import annotations

from api.agents.base import sanitize_agent_text


def test_strips_antcite_tags() -> None:
    s = '<antcite index="16-4">Q1 2026 wasn\'t just an acceleration.</antcite>'
    assert sanitize_agent_text(s) == "Q1 2026 wasn't just an acceleration."


def test_strips_mangled_anite_and_ite() -> None:
    s = '<anite index="16-4">a</anite> and <ite index="16-11,16-12">b</ite>.'
    assert sanitize_agent_text(s) == "a and b."


def test_strips_self_closing_and_with_attrs() -> None:
    s = 'before <antcite index="1-2"> middle </antcite> after'
    assert sanitize_agent_text(s) == "before  middle  after"


def test_leaves_normal_html_alone() -> None:
    s = "Plain markdown with <em>emphasis</em> and <a href='x'>link</a>."
    # We only target the cite-family tags; <em>/<a> survive.
    assert sanitize_agent_text(s) == s


def test_idempotent_on_clean_text() -> None:
    s = "No tags here, just words and [a markdown link](https://example.com)."
    assert sanitize_agent_text(s) == s
