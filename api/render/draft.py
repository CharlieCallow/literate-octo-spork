"""Editable intermediate artifact for reports.

The pipeline is:

    agents -> draft.md (+ charts/) -> [human edit] -> render -> report.pdf

`draft.md` is the source of truth. It is a plain markdown file with a YAML
frontmatter block. The render step is deterministic, idempotent and makes
no LLM calls -- a typo fix in the markdown plus `python -m scripts.render_report
<dir>` produces a corrected PDF in seconds.

Format:

    ---
    title: "..."
    subtitle: "..."
    date: 2026-05-01
    read_minutes: 8
    hide_bylines: false
    hide_positions: false
    hide_disclosures: false
    contributors:
      - {name: "...", role: "..."}
    positions:
      - {asset: "...", direction: "long", horizon_days: 90,
         target: 220.0, conviction: 4}
    sources:
      - {n: 1, url: "...", title: "...", source: "..."}
    ---

    # HOUSE VIEW (TOP)
    ...

    # SECTIONS

    ## Opening
    ...

    ## Macro view
    **author:** Henrik Voss
    **role:** Macro Strategist

    body, with chart references like [chart: rates.png]
    ...

    # DISAGREEMENT
    ...

    # BEAR CASE
    ...

    # HOUSE VIEW (BOTTOM)
    ...

    # GLOSSARY
    ...

Charts are referenced by filename (`[chart: rates.png]`) and resolved
against `<dir>/charts/`. Editing the prose does NOT regenerate the chart
PNGs."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from api.render.pdf import Contributor, Section, render_pdf


@dataclass
class Draft:
    title: str
    subtitle: str
    date: str
    contributors: list[Contributor] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    house_view_top: str | None = None
    house_view_bottom: str | None = None
    disagreement: str | None = None
    bear_case: str | None = None
    glossary: str | None = None
    positions: list[dict[str, Any]] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    read_minutes: int = 8
    hide_bylines: bool = False
    hide_positions: bool = False
    hide_disclosures: bool = False


# ---------- chart tag <-> figure block round-trip ----------

_CHART_TAG_RE = re.compile(r"\[chart:\s*(?P<name>[^\]]+?)\s*\]")
# Matches the <figure><img src="file://.../charts/X.png" ...></figure> blocks
# that the workflow inlines before render. Used to undo that inlining when we
# write draft.md so the editable artifact stays human-friendly.
_FIGURE_BLOCK_RE = re.compile(
    r"\n?<figure>\s*<img\s+[^>]*src=\"[^\"]*?/charts/(?P<name>[^/\"]+?)\"[^>]*>\s*</figure>\n?",
    re.IGNORECASE,
)


def figures_to_chart_tags(body: str) -> str:
    """Inverse of `inline_chart_tags`. Replaces inlined <figure> blocks
    with the `[chart: name.png]` shorthand so the prose round-trips into
    draft.md as a human can read it."""
    return _FIGURE_BLOCK_RE.sub(lambda m: f"\n\n[chart: {m.group('name')}]\n\n", body)


def inline_chart_tags(body: str, charts_dir: Path) -> str:
    """Replace `[chart: name.png]` shorthand with <figure><img> blocks
    pointing at `charts_dir`. Missing files are dropped silently rather
    than substituted -- a wrong chart reads worse than no chart."""
    used: set[str] = set()
    charts_dir = charts_dir.resolve()

    def repl(m: re.Match[str]) -> str:
        name = m.group("name").strip()
        path = charts_dir / name
        if not path.exists() or name in used:
            return ""
        used.add(name)
        return f'\n<figure><img src="{path.as_uri()}" alt="{name}"></figure>\n'

    return _CHART_TAG_RE.sub(repl, body)


# ---------- dump ----------

# Whitespace-tolerant matchers for the per-section author/role lines so a
# human re-formatting them survives parse.
_AUTHOR_RE = re.compile(r"^\s*\*\*author:\*\*\s*(.+?)\s*$", re.M)
_ROLE_RE = re.compile(r"^\s*\*\*role:\*\*\s*(.+?)\s*$", re.M)


def _section_block(s: Section) -> str:
    lines = [f"## {s.heading}", ""]
    if s.author:
        lines.append(f"**author:** {s.author}")
    if s.role:
        lines.append(f"**role:** {s.role}")
    if s.author or s.role:
        lines.append("")
    lines.append(s.body_md.strip())
    return "\n".join(lines).rstrip() + "\n"


def _maybe_block(label: str, text: str | None) -> str:
    if not text or not text.strip():
        return ""
    return f"# {label}\n\n{text.strip()}\n"


def dump_draft(draft: Draft, out_dir: Path) -> Path:
    """Write `<out_dir>/draft.md`. Section bodies are written with chart
    references in `[chart: name.png]` form (the human-friendly shorthand),
    not as inlined <figure> blocks."""
    fm: dict[str, Any] = {
        "title": draft.title,
        "subtitle": draft.subtitle,
        "date": draft.date,
        "read_minutes": draft.read_minutes,
        "hide_bylines": draft.hide_bylines,
        "hide_positions": draft.hide_positions,
        "hide_disclosures": draft.hide_disclosures,
        "contributors": [{"name": c.name, "role": c.role} for c in draft.contributors],
        "positions": list(draft.positions),
        "sources": list(draft.sources),
    }
    fm_text = yaml.safe_dump(
        fm, sort_keys=False, allow_unicode=True, default_flow_style=False, width=10_000,
    )

    parts: list[str] = []
    parts.append(_maybe_block("HOUSE VIEW (TOP)", draft.house_view_top))
    if draft.sections:
        parts.append("# SECTIONS\n")
        for s in draft.sections:
            # Body is dumped with chart shorthand. If the workflow handed us a
            # body that already had inlined <figure> blocks, undo that here so
            # draft.md stays human-readable.
            body = figures_to_chart_tags(s.body_md)
            parts.append(_section_block(
                Section(heading=s.heading, body_md=body, author=s.author, role=s.role)
            ))
    parts.append(_maybe_block("DISAGREEMENT", draft.disagreement))
    parts.append(_maybe_block("BEAR CASE", draft.bear_case))
    parts.append(_maybe_block("HOUSE VIEW (BOTTOM)", draft.house_view_bottom))
    parts.append(_maybe_block("GLOSSARY", draft.glossary))

    body_text = "\n".join(p for p in parts if p)
    text = f"---\n{fm_text}---\n\n{body_text}"

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "draft.md"
    out_path.write_text(text, encoding="utf-8")
    return out_path


# ---------- load ----------

_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.S)
# Top-level ALL-CAPS heading. Anchored to a line start; stops at the next
# top-level heading or end of document. `# SECTIONS` is matched separately
# because its body is parsed into individual `## ` blocks.
_TOPLEVEL_RE_TEMPLATE = r"^# {label}\s*\n(.+?)(?=\n# [A-Z]|\Z)"
_SECTION_RE = re.compile(r"^# SECTIONS\s*\n(.+?)(?=\n# [A-Z]|\Z)", re.S | re.M)
_PER_SECTION_RE = re.compile(
    r"^## (?P<heading>.+?)\n(?P<body>.+?)(?=\n## |\Z)", re.S | re.M,
)


def _grab(label: str, body: str) -> str | None:
    m = re.search(
        _TOPLEVEL_RE_TEMPLATE.format(label=re.escape(label)),
        body, re.S | re.M,
    )
    if not m:
        return None
    return m.group(1).strip() or None


_LABELS = {
    "house_view_top": "HOUSE VIEW (TOP)",
    "house_view_bottom": "HOUSE VIEW (BOTTOM)",
    "disagreement": "DISAGREEMENT",
    "bear_case": "BEAR CASE",
    "glossary": "GLOSSARY",
}


def _parse_section_blocks(blob: str) -> list[Section]:
    out: list[Section] = []
    for m in _PER_SECTION_RE.finditer(blob):
        heading = m.group("heading").strip()
        body = m.group("body")
        author_m = _AUTHOR_RE.search(body)
        role_m = _ROLE_RE.search(body)
        # Strip the metadata lines from the body.
        body_clean = body
        if author_m:
            body_clean = body_clean.replace(author_m.group(0), "", 1)
        if role_m:
            body_clean = body_clean.replace(role_m.group(0), "", 1)
        out.append(Section(
            heading=heading,
            body_md=body_clean.strip(),
            author=author_m.group(1).strip() if author_m else None,
            role=role_m.group(1).strip() if role_m else None,
        ))
    return out


def load_draft(path: Path) -> Draft:
    """Parse `draft.md`. Accepts either a path to the file or to its
    parent directory."""
    if path.is_dir():
        path = path / "draft.md"
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise ValueError(f"{path}: missing YAML frontmatter")
    fm = yaml.safe_load(m.group(1)) or {}
    body = m.group(2)

    contributors = [
        Contributor(name=str(c["name"]), role=str(c["role"]))
        for c in (fm.get("contributors") or [])
    ]
    sec_blob_m = _SECTION_RE.search(body)
    sections = _parse_section_blocks(sec_blob_m.group(1)) if sec_blob_m else []

    return Draft(
        title=str(fm.get("title", "")),
        subtitle=str(fm.get("subtitle", "")),
        date=str(fm.get("date", "")),
        contributors=contributors,
        sections=sections,
        house_view_top=_grab(_LABELS["house_view_top"], body),
        house_view_bottom=_grab(_LABELS["house_view_bottom"], body),
        disagreement=_grab(_LABELS["disagreement"], body),
        bear_case=_grab(_LABELS["bear_case"], body),
        glossary=_grab(_LABELS["glossary"], body),
        positions=list(fm.get("positions") or []),
        sources=list(fm.get("sources") or []),
        read_minutes=int(fm.get("read_minutes", 8)),
        hide_bylines=bool(fm.get("hide_bylines", False)),
        hide_positions=bool(fm.get("hide_positions", False)),
        hide_disclosures=bool(fm.get("hide_disclosures", False)),
    )


# ---------- render ----------

def render_draft(draft: Draft, charts_dir: Path, out_path: Path) -> Path:
    """Render a Draft to PDF. Expands `[chart: name.png]` tags against
    `charts_dir`. No LLM calls, no network."""
    expanded = [
        Section(
            heading=s.heading,
            body_md=inline_chart_tags(s.body_md, charts_dir),
            author=s.author,
            role=s.role,
        )
        for s in draft.sections
    ]
    return render_pdf(
        out_path=out_path,
        title=draft.title,
        subtitle=draft.subtitle,
        date=draft.date,
        contributors=draft.contributors,
        sections=expanded,
        house_view_top=draft.house_view_top,
        house_view_bottom=draft.house_view_bottom,
        disagreement=draft.disagreement,
        bear_case=draft.bear_case,
        glossary=draft.glossary,
        positions=draft.positions or None,
        read_minutes=draft.read_minutes,
        sources=draft.sources or None,
        hide_bylines=draft.hide_bylines,
        hide_positions=draft.hide_positions,
        hide_disclosures=draft.hide_disclosures,
    )


def render_from_dir(report_dir: Path, out_path: Path | None = None) -> Path:
    """Convenience: load `<report_dir>/draft.md`, expand chart tags
    against `<report_dir>/charts/`, render to `<report_dir>/report.pdf`
    (or `out_path` if provided)."""
    draft = load_draft(report_dir)
    out = out_path or (report_dir / "report.pdf")
    return render_draft(draft, report_dir / "charts", out)


__all__ = [
    "Draft",
    "dump_draft",
    "load_draft",
    "render_draft",
    "render_from_dir",
    "inline_chart_tags",
    "figures_to_chart_tags",
]


# Suppress unused-import warning -- asdict is exported for callers that want
# to introspect a Draft.
_ = asdict
