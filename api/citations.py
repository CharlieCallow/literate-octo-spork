"""Inline citation insertion.

Agents write claims like `Spot uranium up 220% off lows ([Cameco Q3 release](https://...))`.
At render time we walk every section, replace each markdown link with a
superscript citation number anchored to the Sources section, and return an
ordered citation list (cited URLs first, then any uncited extras from
sources.json so they still show up at the end of the report).

URLs that fail a HEAD-check are kept as plain text (the agent's anchor text
remains) but get dropped from the Sources list -- inaccessible links shouldn't
clutter the appendix even if the model relied on them at research time.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace

from api.render.pdf import Section
from api.url_check import check_reachable

LINK_RE = re.compile(r"\[(?P<text>[^\]]+?)\]\((?P<url>https?://[^)\s]+)\)")


def _collect_urls(
    sections: Sequence[Section],
    extra_sources: Sequence[dict[str, str | None]] | None,
) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for s in sections:
        for m in LINK_RE.finditer(s.body_md):
            url = m.group("url").rstrip(".,;)")
            if url not in seen:
                seen.add(url)
                urls.append(url)
    for src in extra_sources or []:
        u = src.get("url")
        if u and u not in seen:
            seen.add(u)
            urls.append(u)
    return urls


def attach_inline_citations(
    sections: Sequence[Section],
    extra_sources: Sequence[dict[str, str | None]] | None = None,
    *,
    check_urls: bool = True,
) -> tuple[list[Section], list[dict[str, str | None]]]:
    """Replace markdown links in section bodies with anchored superscript
    citations. Returns the rewritten sections plus a numbered sources list.

    URLs that fail a reachability HEAD-check (when `check_urls=True`) keep
    their visible anchor text but drop the superscript number, and don't
    appear in the returned sources list."""
    reachable: set[str] = set()
    if check_urls:
        reachable = check_reachable(_collect_urls(sections, extra_sources))

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
            if check_urls and url not in reachable:
                # Dead link -- keep the visible anchor text, drop the citation
                # marker so the reader isn't pointed at a 404.
                return text
            num = get_or_assign(url, text, "inline")
            return f'{text}<sup class="cite"><a href="#cite-{num}">[{num}]</a></sup>'

        new_sections.append(replace(s, body_md=LINK_RE.sub(repl, s.body_md)))

    # Append uncited URLs from sources.json (web search hits etc.) so they're
    # still visible in the Sources section -- but only if they're reachable.
    for src in extra_sources or []:
        url = src.get("url")
        if not url or url in inline_urls:
            continue
        if check_urls and url not in reachable:
            continue
        get_or_assign(url, src.get("title"), src.get("source") or "web")

    sources = sorted(inline_urls.values(), key=lambda s: int(s["n"] or "0"))
    return new_sections, sources
