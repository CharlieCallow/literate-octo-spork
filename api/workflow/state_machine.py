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
from api.models import AuditLog, Report, ReportStage
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


def working_dir(report_id: int) -> Path:
    d = settings.reports_dir / str(report_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "charts").mkdir(exist_ok=True)
    return d


def _read(p: Path) -> str:
    return p.read_text() if p.exists() else ""


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


def run_stage(report: Report, stage: ReportStage) -> ReportStage:
    """Execute one stage. Returns the next stage to run (or `done`)."""
    if report.id is None:
        raise ValueError("Report has no id")

    wd = working_dir(report.id)
    audit = audit_hook(report.id)
    cost = _tracker()

    if stage == ReportStage.brief:
        eic = EditorInChief(cost, audit=audit)
        result = eic.write_brief(report.theme, subtitle=report.subtitle)
        (wd / "brief.md").write_text(result.text)
        _persist_cost(report.id, result.cost_usd)
        return _next_stage(stage)

    if stage == ReportStage.research:
        macro = Analyst("macro-strategist.md", cost, audit=audit)
        brief = _read(wd / "brief.md")
        result = macro.research(brief, report.theme, wd)
        (wd / "notes-macro.md").write_text(result.text)
        _persist_cost(report.id, result.cost_usd)
        return _next_stage(stage)

    if stage == ReportStage.charts:
        dc = DataAndCharts(cost, audit=audit)
        brief = _read(wd / "brief.md")
        notes = _read(wd / "notes-macro.md")
        result = dc.build(brief, notes, wd / "charts")
        (wd / "data-section.md").write_text(result.text)
        _persist_cost(report.id, result.cost_usd)
        return _next_stage(stage)

    if stage == ReportStage.draft:
        macro = Analyst("macro-strategist.md", cost, audit=audit)
        brief = _read(wd / "brief.md")
        notes = _read(wd / "notes-macro.md")
        result = macro.draft(brief, notes, report.theme)
        (wd / "section-macro.md").write_text(result.text)
        _persist_cost(report.id, result.cost_usd)
        return _next_stage(stage)

    if stage == ReportStage.edit:
        eic = EditorInChief(cost, audit=audit)
        brief = _read(wd / "brief.md")
        sections = [
            {
                "heading": _heading(_read(wd / "section-macro.md")) or "Macro view",
                "body": _strip_heading(_read(wd / "section-macro.md")),
                "author": "Henrik Voss",
                "role": "Macro Strategist",
            },
            {
                "heading": "Data & charts",
                "body": _strip_heading(_read(wd / "data-section.md")),
                "author": "Tomás Reyes",
                "role": "Data & Charts",
            },
        ]
        chart_summary = _list_charts(wd / "charts")
        result = eic.edit(brief=brief, sections=sections, chart_summary=chart_summary)
        (wd / "edited.md").write_text(result.text)
        _persist_cost(report.id, result.cost_usd)
        return _next_stage(stage)

    if stage == ReportStage.render:
        edited = _read(wd / "edited.md")
        parsed = parse_edited(edited)
        out = wd / "report.pdf"
        render_pdf(
            out_path=out,
            title=report.theme.title() if report.theme.islower() else report.theme,
            subtitle=report.subtitle or parsed.get("opening", "").split("\n")[0][:120],
            date=date.today().isoformat(),
            contributors=[
                Contributor("Margaux Devlin", "Editor-in-Chief"),
                Contributor("Henrik Voss", "Macro Strategist"),
                Contributor("Tomás Reyes", "Data & Charts"),
            ],
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
        (wd / "feedback.md").write_text("_Feedback log lands in M4._\n")
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
