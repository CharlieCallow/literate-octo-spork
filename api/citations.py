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
from urllib.parse import urlsplit

from api.render.pdf import Section
from api.url_check import check_reachable

LINK_RE = re.compile(r"\[(?P<text>[^\]]+?)\]\((?P<url>https?://[^)\s]+)\)")

# Bare-homepage paths the analyst sometimes lands on when citing macro
# claims via web search. The publisher's homepage isn't a real citation, so
# we treat these like dead links: keep the visible anchor text, drop the
# superscript and the entry from Sources.
_HOMEPAGE_PATHS = {"", "/", "/index.html", "/index.htm", "/home", "/en", "/en/"}


def _is_generic_homepage(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.path.rstrip().lower() in _HOMEPAGE_PATHS and not parts.query


def _registrable_domain(url: str) -> str | None:
    """Return host minus a leading 'www.' so 'ft.com' and 'www.ft.com' merge.

    We don't do PSL-aware eTLD+1 trimming because the citation set is small
    and a few sub-domain false-splits are fine. The point is to catch lazy
    research where one publisher dominates."""
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return None
    if not host:
        return None
    return host[4:] if host.startswith("www.") else host


# Primary-source classification.
#
# A "primary" source is one issued by the original publisher of the underlying
# fact: regulators, the issuer itself (10-K, 8-K, IR press release), the
# standards body that ran the measurement (IEEE 802.3, OFC), the research
# group that published the paper. A "secondary" source is an aggregator,
# news rewrite, broker quote page, or SEO market-report mill that re-prints
# someone else's number. The audit stage uses primary_source_share() to
# enforce a 30% floor; below that, the report is rejected and routed back
# to research for a primary-source-only retry pass.

# Domains whose URLs count as primary regardless of path.
_PRIMARY_DOMAINS: frozenset[str] = frozenset({
    # SEC + filings
    "sec.gov", "efts.sec.gov", "data.sec.gov",
    # US macro / regulators
    "fred.stlouisfed.org", "stlouisfed.org",
    "federalreserve.gov", "treasury.gov", "treasurydirect.gov",
    "bls.gov", "bea.gov", "census.gov", "eia.gov", "cbo.gov",
    "fdic.gov", "occ.gov", "cftc.gov", "finra.org",
    # Health / drug regulators
    "fda.gov", "api.fda.gov", "open.fda.gov",
    "clinicaltrials.gov", "nih.gov", "cdc.gov", "ema.europa.eu",
    # Non-US central banks / regulators
    "ecb.europa.eu", "bankofengland.co.uk", "boj.or.jp",
    "snb.ch", "rba.gov.au", "bis.org",
    # Multilateral
    "imf.org", "worldbank.org", "data.worldbank.org",
    "oecd.org", "data.oecd.org", "iea.org", "opec.org",
    # Research / standards
    "ieee.org", "ieeexplore.ieee.org", "standards.ieee.org",
    "optica.org", "opg.optica.org",  # OFC is run by Optica
    "ofcconference.org",
    "arxiv.org", "biorxiv.org", "medrxiv.org", "ssrn.com",
    "doi.org", "nature.com", "science.org", "nejm.org",
    "acm.org", "dl.acm.org",
    # Energy / commodities regulators
    "ferc.gov", "nrc.gov",
    # CFTC commitments
    "publicreporting.cftc.gov",
})

# Domains whose URLs are explicitly secondary (aggregators, quote pages,
# SEO mills). These never count as primary even if the path looks deep.
_SECONDARY_DOMAINS: frozenset[str] = frozenset({
    "finance.yahoo.com", "uk.finance.yahoo.com",
    "seekingalpha.com", "fool.com", "investorplace.com",
    "marketwatch.com", "cnbc.com", "barrons.com",
    "thestreet.com", "zacks.com", "benzinga.com",
    "financialcontent.com", "stockanalysis.com",
    "tradingview.com", "investing.com", "morningstar.com",
    "simplywall.st", "tipranks.com", "wallstreetzen.com",
    # SEO market-report mills
    "marketsandmarkets.com", "grandviewresearch.com",
    "fortunebusinessinsights.com", "mordorintelligence.com",
    "alliedmarketresearch.com", "marketresearch.com",
    "researchandmarkets.com", "globenewswire.com",
    "prnewswire.com", "businesswire.com",  # press-release wires; primary IR pages preferred
})

# Path / host fragments that promote a domain to primary even when the
# registrable domain isn't on the explicit list. Catches company IR sites
# served from the corporate domain (`investors.broadcom.com`,
# `news.cisco.com/press-release/...`) without us having to enumerate
# every issuer.
_PRIMARY_HOST_PREFIXES: tuple[str, ...] = (
    "investor.", "investors.", "ir.", "press.", "newsroom.", "news.",
)
_PRIMARY_PATH_HINTS: tuple[str, ...] = (
    "/investor", "/investors", "/press-release", "/press-releases",
    "/news-release", "/news-releases", "/newsroom", "/sec-filings",
)


def is_primary_source(url: str | None) -> bool:
    """True if the URL is a primary source (regulator, issuer IR, standards
    body, research paper). Secondary aggregators and quote pages return False.

    Heuristic, not exhaustive -- the registrable-domain allow/deny lists
    plus a host-prefix / path-hint check cover the cases the analyst tools
    actually produce. Unknown domains default to False so the quota errs
    on the side of demanding more primary citations, not fewer."""
    if not url:
        return False
    dom = _registrable_domain(url)
    if not dom:
        return False
    if dom in _SECONDARY_DOMAINS:
        return False
    if dom in _PRIMARY_DOMAINS:
        return True
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if any(host.startswith(p) for p in _PRIMARY_HOST_PREFIXES):
        return True
    path = parts.path.lower()
    if any(h in path for h in _PRIMARY_PATH_HINTS):
        return True
    return False


def primary_source_share(sources: list[dict[str, str | None]]) -> tuple[int, int, float]:
    """Return (n_primary, n_total, share). Empty input returns (0, 0, 0.0).

    Used by the audit stage to enforce the 30% primary-source floor."""
    total = 0
    primary = 0
    for s in sources:
        if not isinstance(s, dict):
            continue
        url = s.get("url")
        if not url:
            continue
        total += 1
        if is_primary_source(url):
            primary += 1
    share = (primary / total) if total else 0.0
    return primary, total, share


def domain_distribution(sources: list[dict[str, str | None]]) -> tuple[str | None, float]:
    """Return (top_domain, share). Empty inputs return (None, 0.0).

    Used post-citation to flag reports where one publisher accounts for a
    suspicious share of the citations -- a 'Bloomberg-only' research smell
    the prompt won't catch."""
    counts: dict[str, int] = {}
    total = 0
    for s in sources:
        url = s.get("url") if isinstance(s, dict) else None
        if not url:
            continue
        dom = _registrable_domain(url)
        if not dom:
            continue
        counts[dom] = counts.get(dom, 0) + 1
        total += 1
    if total == 0:
        return None, 0.0
    top = max(counts.items(), key=lambda kv: kv[1])
    return top[0], top[1] / total


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
            if _is_generic_homepage(url):
                # Bare homepage -- not a real citation. Same treatment as a
                # dead link: keep anchor text, drop the superscript.
                return text
            num = get_or_assign(url, text, "inline")
            return f'{text}<sup class="cite"><a href="#cite-{num}">[{num}]</a></sup>'

        new_sections.append(replace(s, body_md=LINK_RE.sub(repl, s.body_md)))

    # Append uncited URLs from sources.json (web search hits etc.) so they're
    # still visible in the Sources section -- but only if they're reachable
    # and not a bare publisher homepage.
    for src in extra_sources or []:
        url = src.get("url")
        if not url or url in inline_urls:
            continue
        if check_urls and url not in reachable:
            continue
        if _is_generic_homepage(url):
            continue
        get_or_assign(url, src.get("title"), src.get("source") or "web")

    sources = sorted(inline_urls.values(), key=lambda s: int(s["n"] or "0"))
    return new_sections, sources
