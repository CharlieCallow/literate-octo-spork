"""Inline citation insertion.

Agents write claims like `Spot uranium up 220% off lows ([Cameco Q3 release](https://...))`.
At render time we walk every section, replace each markdown link with a
superscript citation number anchored to the Sources section, and return an
ordered citation list (cited URLs first, then any uncited extras from
sources.json so they still show up at the end of the report).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace

from api.render.pdf import Section

LINK_RE = re.compile(r"\[(?P<text>[^\]]+?)\]\((?P<url>https?://[^)\s]+)\)")


def attach_inline_citations(
    sections: Sequence[Section],
    extra_sources: Sequence[dict[str, str | None]] | None = None,
) -> tuple[list[Section], list[dict[str, str | None]]]:
    """Replace markdown links in section bodies with anchored superscript
    citations. Returns the rewritten sections plus a numbered sources list."""
    inline_urls: dict[str, dict[str, str | None]] = {}
    counter = {"n": 0}

    def get_or_assign(url: str, title: str | None, source: str) -> int:
        if url not in inline_urls:
            counter["n"] += 1
            inline_urls[url] = {
                "n": str(counter["n"]),
                "url": url,
                "title": title or url,
                "source": source,
            }
        return int(inline_urls[url]["n"] or "0")

    new_sections: list[Section] = []
    for s in sections:
        def repl(m: re.Match[str]) -> str:
            text = m.group("text")
            url = m.group("url").rstrip(".,;)")
            num = get_or_assign(url, text, "inline")
            return f'{text}<sup class="cite"><a href="#cite-{num}">[{num}]</a></sup>'

        new_sections.append(replace(s, body_md=LINK_RE.sub(repl, s.body_md)))

    # Append uncited URLs from sources.json (web search hits etc.) so they're
    # still visible in the Sources section, just after the inline-cited ones.
    for src in extra_sources or []:
        url = src.get("url")
        if not url or url in inline_urls:
            continue
        get_or_assign(url, src.get("title"), src.get("source") or "web")

    sources = sorted(inline_urls.values(), key=lambda s: int(s["n"] or "0"))
    return new_sections, sources
