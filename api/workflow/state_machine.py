"""Workflow state machine. Each stage reads/writes the report's working dir
and updates the Report row in the DB. Stages are idempotent — re-running
a stage overwrites its output and does not corrupt earlier stages."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from sqlmodel import Session, select

from api.agents.analyst import Analyst
from api.agents.charts import DataAndCharts
from api.agents.cost import CostTracker
from api.agents.editor import EditorInChief
from api.db import engine
from api.models import AuditLog, Report, ReportMode, ReportStage
from api.render.pdf import Contributor, Section, render_pdf
from api.settings import settings
from api.workflow.audit import audit_hook

STAGE_ORDER: list[ReportStage] = [
    ReportStage.brief,
    ReportStage.research,
    ReportStage.charts,
    ReportStage.draft,
    ReportStage.edit,
    ReportStage.render,
    ReportStage.feedback,
    ReportStage.done,
]


# ---------------------------------------------------------------------------
# Roster — analysts the EIC can put on a report.
# Slugs match the persona filename without `.md`. Names are display names used
# on the cover page and section bylines.
# ---------------------------------------------------------------------------

ROSTER: list[dict[str, str]] = [
    {"slug": "macro-strategist", "name": "Henrik Voss",  "role": "Macro Strategist"},
    {"slug": "equity-analyst",   "name": "Priya Anand",  "role": "Equity / Sector Analyst"},
]

ROSTER_BY_SLUG: dict[str, dict[str, str]] = {c["slug"]: c for c in ROSTER}

EIC_DISPLAY = {"name": "Margaux Devlin", "role": "Editor-in-Chief"}
DC_DISPLAY  = {"name": "Tomás Reyes",    "role": "Data & Charts"}


def working_dir(report_id: int) -> Path:
    d = settings.reports_dir / str(report_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "charts").mkdir(exist_ok=True)
    return d


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _write(p: Path, text: str) -> None:
    p.write_text(text, encoding="utf-8")


def _today_spent() -> float:
    today = date.today()
    with Session(engine) as session:
        rows = session.exec(select(AuditLog)).all()
    return sum(r.cost_usd for r in rows if r.created_at.date() == today)


def _tracker() -> CostTracker:
    return CostTracker(
        report_cap=settings.cost_per_report_usd,
        day_cap=settings.cost_per_day_usd,
        day_spent=_today_spent(),
    )


def _persist_cost(report_id: int, delta: float) -> None:
    with Session(engine) as session:
        report = session.get(Report, report_id)
        if not report:
            return
        report.cost_usd += delta
        session.add(report)
        session.commit()


def _next_stage(stage: ReportStage) -> ReportStage:
    i = STAGE_ORDER.index(stage)
    return STAGE_ORDER[i + 1]


def _models_for(mode: ReportMode) -> dict[str, str]:
    """Pick model IDs per stage based on report mode.
    `fast` runs Haiku end-to-end (testing); `standard` uses Opus EIC + Sonnet."""
    if mode == ReportMode.fast:
        m = settings.model_haiku
        return {"editor": m, "analyst": m, "data": m}
    return {
        "editor": settings.model_opus,
        "analyst": settings.model_sonnet,
        "data": settings.model_sonnet,
    }


def _make_analyst(slug: str, cost: CostTracker, audit, model: str) -> Analyst:  # type: ignore[no-untyped-def]
    return Analyst(f"{slug}.md", cost, audit=audit, model=model)


def _resolved_contributors(brief: str) -> list[dict[str, str]]:
    """Read the brief's CONTRIBUTORS section and resolve to roster entries.
    Falls back to the full roster if the brief is missing or empty."""
    parsed = parse_brief(brief)
    slugs = parsed["contributor_slugs"]
    valid = [ROSTER_BY_SLUG[s] for s in slugs if s in ROSTER_BY_SLUG]
    return valid or list(ROSTER)


def run_stage(report: Report, stage: ReportStage) -> ReportStage:
    """Execute one stage. Returns the next stage to run (or `done`)."""
    if report.id is None:
        raise ValueError("Report has no id")

    wd = working_dir(report.id)
    audit = audit_hook(report.id)
    cost = _tracker()
    models = _models_for(report.mode)
    is_fast = report.mode == ReportMode.fast

    if stage == ReportStage.brief:
        eic = EditorInChief(cost, audit=audit, model=models["editor"])
        result = eic.write_brief(
            report.theme,
            subtitle=report.subtitle,
            available_contributors=ROSTER,
        )
        _write(wd / "brief.md", result.text)
        # Lift subtitle out of the brief if user didn't supply one.
        if not report.subtitle:
            sub = parse_brief(result.text).get("subtitle")
            if sub:
                with Session(engine) as session:
                    r = session.get(Report, report.id)
                    if r and not r.subtitle:
                        r.subtitle = sub
                        session.add(r); session.commit()
        _persist_cost(report.id, result.cost_usd)
        return _next_stage(stage)

    if stage == ReportStage.research:
        brief = _read(wd / "brief.md")
        contributors = _resolved_contributors(brief)
        for c in contributors:
            analyst = _make_analyst(c["slug"], cost, audit, models["analyst"])
            result = analyst.research(brief, report.theme, wd, fast=is_fast)
            _write(wd / f"notes-{c['slug']}.md", result.text)
            _persist_cost(report.id, result.cost_usd)
        return _next_stage(stage)

    if stage == ReportStage.charts:
        dc = DataAndCharts(cost, audit=audit, model=models["data"])
        brief = _read(wd / "brief.md")
        all_notes = _concat_notes(wd, _resolved_contributors(brief))
        result = dc.build(brief, all_notes, wd / "charts", fast=is_fast)
        _write(wd / "data-section.md", result.text)
        _persist_cost(report.id, result.cost_usd)
        return _next_stage(stage)

    if stage == ReportStage.draft:
        brief = _read(wd / "brief.md")
        contributors = _resolved_contributors(brief)
        for c in contributors:
            analyst = _make_analyst(c["slug"], cost, audit, models["analyst"])
            notes = _read(wd / f"notes-{c['slug']}.md")
            result = analyst.draft(brief, notes, report.theme)
            _write(wd / f"section-{c['slug']}.md", result.text)
            _persist_cost(report.id, result.cost_usd)
        return _next_stage(stage)

    if stage == ReportStage.edit:
        eic = EditorInChief(cost, audit=audit, model=models["editor"])
        brief = _read(wd / "brief.md")
        contributors = _resolved_contributors(brief)
        sections: list[dict[str, str]] = []
        # Only analyst sections go through the EIC. The Data & Charts section
        # passes through verbatim so chart references survive.
        for c in contributors:
            body_md = _read(wd / f"section-{c['slug']}.md")
            if not body_md.strip():
                continue
            sections.append({
                "heading": _heading(body_md) or c["role"],
                "body": _strip_heading(body_md),
                "author": c["name"],
                "role": c["role"],
            })
        chart_summary = _list_charts(wd / "charts")
        result = eic.edit(brief=brief, sections=sections, chart_summary=chart_summary)
        _write(wd / "edited.md", result.text)
        _persist_cost(report.id, result.cost_usd)
        return _next_stage(stage)

    if stage == ReportStage.render:
        edited = _read(wd / "edited.md")
        brief = _read(wd / "brief.md")
        parsed = parse_edited(edited)
        out = wd / "report.pdf"
        contributors_credits = [
            Contributor(EIC_DISPLAY["name"], EIC_DISPLAY["role"]),
            *[Contributor(c["name"], c["role"]) for c in _resolved_contributors(brief)],
            Contributor(DC_DISPLAY["name"], DC_DISPLAY["role"]),
        ]
        # Reload report to pick up subtitle written during the brief stage.
        with Session(engine) as session:
            r = session.get(Report, report.id)
            subtitle = r.subtitle if r else None
        render_pdf(
            out_path=out,
            title=report.theme.title() if report.theme.islower() else report.theme,
            subtitle=subtitle or parsed.get("opening", "").split("\n")[0][:120],
            date=date.today().isoformat(),
            contributors=contributors_credits,
            sections=_sections_for_render(parsed, wd),
            house_view_top=parsed.get("house_view_top"),
            house_view_bottom=parsed.get("house_view_bottom"),
            read_minutes=8,
        )
        with Session(engine) as session:
            r = session.get(Report, report.id)
            if r:
                r.pdf_path = str(out)
                session.add(r)
                session.commit()
        return _next_stage(stage)

    if stage == ReportStage.feedback:
        # M4 — for now just leave a placeholder log entry.
        _write(wd / "feedback.md", "_Feedback log lands in M4._\n")
        return _next_stage(stage)

    return ReportStage.done


# ---------- helpers ----------

def _heading(md: str) -> str | None:
    for line in md.splitlines():
        if line.startswith("## "):
            return line[3:].strip()
    return None


def _strip_heading(md: str) -> str:
    lines = md.splitlines()
    if lines and lines[0].startswith("## "):
        return "\n".join(lines[1:]).lstrip("\n")
    return md


def _list_charts(charts_dir: Path) -> str:
    if not charts_dir.exists():
        return "No charts produced."
    files = sorted(p.name for p in charts_dir.iterdir() if p.suffix.lower() == ".png")
    return "\n".join(f"- {f}" for f in files) or "No charts produced."


def _concat_notes(wd: Path, contributors: list[dict[str, str]]) -> str:
    parts: list[str] = []
    for c in contributors:
        text = _read(wd / f"notes-{c['slug']}.md")
        if text.strip():
            parts.append(f"## NOTES FROM {c['name']} ({c['role']})\n\n{text}")
    return "\n\n---\n\n".join(parts) if parts else "(no analyst notes)"


# ---------- brief parsing ----------

_BRIEF_SECTION_RE = re.compile(r"^# (?P<head>[A-Z][A-Z ()]+)\s*\n(?P<body>.+?)(?=\n# |\Z)", re.S | re.M)
_BRIEF_SLUG_RE   = re.compile(r"`([a-z0-9][a-z0-9-]*)`")


def parse_brief(text: str) -> dict[str, object]:
    sections: dict[str, str] = {}
    for m in _BRIEF_SECTION_RE.finditer(text):
        sections[m.group("head").strip()] = m.group("body").strip()

    contributor_slugs: list[str] = []
    if c_block := sections.get("CONTRIBUTORS"):
        seen: set[str] = set()
        for slug in _BRIEF_SLUG_RE.findall(c_block):
            if slug not in seen:
                contributor_slugs.append(slug)
                seen.add(slug)

    subtitle = (sections.get("SUBTITLE") or "").strip().splitlines()[0].strip() if sections.get("SUBTITLE") else ""

    return {
        "angle": sections.get("ANGLE", "").strip(),
        "subtitle": subtitle,
        "questions": sections.get("QUESTIONS", "").strip(),
        "contributor_slugs": contributor_slugs,
        "charts": sections.get("CHARTS", "").strip(),
        "data_sources": sections.get("DATA SOURCES", "").strip(),
        "structure": sections.get("STRUCTURE", "").strip(),
    }


# ---------- edited markdown parsing ----------

_HOUSE_TOP_RE = re.compile(r"^# HOUSE VIEW \(TOP\)\s*\n(.+?)(?=\n# |\Z)", re.S | re.M)
_HOUSE_BOT_RE = re.compile(r"^# HOUSE VIEW \(BOTTOM\)\s*\n(.+?)(?=\n# |\Z)", re.S | re.M)
_OPENING_RE  = re.compile(r"^# OPENING\s*\n(.+?)(?=\n# |\Z)", re.S | re.M)
_CLOSING_RE  = re.compile(r"^# CLOSING\s*\n(.+?)(?=\n# |\Z)", re.S | re.M)
_REVISED_RE  = re.compile(r"^# REVISED SECTIONS\s*\n(.+?)(?=\n# HOUSE VIEW \(BOTTOM\)|\Z)", re.S | re.M)
_SECTION_BLOCK_RE = re.compile(
    r"^## (?P<heading>.+?)\n+\*\*author:\*\*\s*(?P<author>.+?)\n+\*\*role:\*\*\s*(?P<role>.+?)\n+(?P<body>.+?)(?=\n## |\Z)",
    re.S | re.M,
)


def parse_edited(text: str) -> dict[str, object]:
    """Parse the EIC's structured markdown output."""

    def grab(rx: re.Pattern[str]) -> str:
        m = rx.search(text)
        return (m.group(1).strip() if m else "")

    revised = grab(_REVISED_RE)
    sections = []
    for m in _SECTION_BLOCK_RE.finditer(revised):
        sections.append({
            "heading": m.group("heading").strip(),
            "author": m.group("author").strip(),
            "role": m.group("role").strip(),
            "body": m.group("body").strip(),
        })
    return {
        "opening": grab(_OPENING_RE),
        "house_view_top": grab(_HOUSE_TOP_RE),
        "sections": sections,
        "house_view_bottom": grab(_HOUSE_BOT_RE),
        "closing": grab(_CLOSING_RE),
    }


def _sections_for_render(parsed: dict[str, object], wd: Path) -> list[Section]:
    out: list[Section] = []
    if opening := parsed.get("opening"):
        out.append(Section(heading="Opening", body_md=str(opening)))

    sections = parsed.get("sections", [])
    for s in sections:  # type: ignore[union-attr]
        body = _inline_charts(str(s["body"]), wd)
        out.append(Section(
            heading=str(s["heading"]),
            body_md=body,
            author=str(s["author"]),
            role=str(s["role"]),
        ))

    # Data & charts section is appended verbatim (skips EIC edit so chart
    # references survive). Sits between the analyst sections and the closing.
    data_md = _read(wd / "data-section.md").strip()
    if data_md:
        body = _inline_charts(_strip_heading(data_md), wd)
        out.append(Section(
            heading="Data & charts",
            body_md=body,
            author=DC_DISPLAY["name"],
            role=DC_DISPLAY["role"],
        ))

    if closing := parsed.get("closing"):
        out.append(Section(heading="Closing", body_md=str(closing)))
    return out


_CHART_TAG_RE = re.compile(r"\[chart:\s*(.+?)\s*\]")


def _inline_charts(body: str, wd: Path) -> str:
    def repl(m: re.Match[str]) -> str:
        fname = m.group(1)
        path = wd / "charts" / fname
        if not path.exists():
            return f"_(chart missing: {fname})_"
        return f'<figure><img src="{path.as_uri()}" alt="{fname}"></figure>'

    return _CHART_TAG_RE.sub(repl, body)
