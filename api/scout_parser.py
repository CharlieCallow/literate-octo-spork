"""Parses the Scout's daily-digest markdown into structured themes.

Scout output format -- one block per theme, literal headings the regex finds:

    # THEME 1
    **headline:** Power-grid bottleneck behind the AI capex story
    **why_now:** Three utility CEOs mentioned interconnect queues this week
    **dig_into:** PJM and ERCOT queue data; Vistra/Constellation guidance
    **sources:** https://example.com/a, https://example.com/b

    # THEME 2
    ...
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_THEME_BLOCK_RE = re.compile(
    r"^# THEME \d+\s*\n(?P<body>.+?)(?=\n# THEME \d+|\Z)",
    re.S | re.M,
)
_FIELD_RE = re.compile(r"^\*\*(?P<key>headline|why_now|dig_into|sources)\:\*\*\s*(?P<value>.+?)$", re.M)
_URL_RE = re.compile(r"https?://[^\s,)]+")


@dataclass
class ParsedTheme:
    headline: str
    why_now: str = ""
    dig_into: str = ""
    source_urls: list[str] = field(default_factory=list)


def parse_themes(text: str) -> list[ParsedTheme]:
    """Extract structured themes from a Scout digest. Empty list on no match."""
    out: list[ParsedTheme] = []
    for m in _THEME_BLOCK_RE.finditer(text):
        body = m.group("body")
        fields: dict[str, str] = {}
        for fm in _FIELD_RE.finditer(body):
            fields[fm.group("key")] = fm.group("value").strip()
        if not fields.get("headline"):
            continue
        urls = _URL_RE.findall(fields.get("sources", ""))
        out.append(ParsedTheme(
            headline=fields["headline"],
            why_now=fields.get("why_now", ""),
            dig_into=fields.get("dig_into", ""),
            source_urls=urls,
        ))
    return out
