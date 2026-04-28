"""Workflow state machine. Each stage reads/writes the report's working dir
and updates the Report row in the DB. Stages are idempotent — re-running
a stage overwrites its output and does not corrupt earlier stages."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from datetime import date
from pathlib import Path

from sqlmodel import Session, select

from api.agents.analyst import Analyst
from api.agents.base import AgentResult, Citation
from api.agents.charts import DataAndCharts
from api.agents.cost import CostTracker
from api.agents.editor import EditorInChief
from api.agents.recruiter import Recruiter
from api.db import engine
from api.models import AuditLog, Report, ReportMode, ReportStage
from api.render.pdf import Contributor, Section, render_pdf
from api.settings import settings
from api.workflow.audit import audit_hook

log = logging.getLogger("workflow")

STAGE_ORDER: list[ReportStage] = [
    ReportStage.brief,
    ReportStage.recruit,
    ReportStage.research,
    ReportStage.charts,
    ReportStage.draft,
    ReportStage.edit,
    ReportStage.render,
    ReportStage.feedback,
    ReportStage.done,
]


# ---------------------------------------------------------------------------
# Roster -- analysts the EIC can put on a report.
# Derived from team/*.md at call time. The standing-team files live in
# settings.team_dir; ad-hoc temps live in settings.team_dir / "temp".
# Excludes the orchestrator personas (EIC, Scout, Recruiter, Data & Charts).
# ---------------------------------------------------------------------------

# Slugs that are agents-with-roles, not contributors. They have personas but
# don't appear in the roster pickers.
_NON_ROSTER_SLUGS = {"editor-in-chief", "data-and-charts", "scout", "recruiter"}

EIC_DISPLAY = {"name": "Margaux Devlin", "role": "Editor-in-Chief"}
DC_DISPLAY  = {"name": "Tomás Reyes",    "role": "Data & Charts"}


def get_roster() -> list[dict[str, str]]:
    """Active analyst roster (status=standing, non-orchestrator). Reads from
    DB so promotions / firings via the Recruiter take effect immediately.
    Falls back to a FS scan in test contexts where the personas table
    hasn't been created yet."""
    out: list[dict[str, str]] = []
    try:
        from api import personas as personas_module
        from api.models import PersonaStatus
        rows = personas_module.list_by_status(PersonaStatus.standing)
        for p in rows:
            if p.is_orchestrator or p.slug in _NON_ROSTER_SLUGS:
                continue
            out.append({
                "slug": p.slug,
                "name": p.name or p.slug.replace("-", " ").title(),
                "role": p.role or "Analyst",
            })
        if out:
            return out
    except Exception:  # noqa: BLE001 - defensive: DB may not exist (tests)
        pass

    # FS fallback for unseeded tests.
    fs_out: list[dict[str, str]] = []
    if not settings.team_dir.exists():
        return fs_out
    for path in sorted(settings.team_dir.glob("*.md")):
        slug = path.stem
        if slug in _NON_ROSTER_SLUGS:
            continue
        text = path.read_text(encoding="utf-8")
        m = _HEADER_RE.search(text)
        if m:
            fs_out.append({"slug": slug, "name": m.group("name").strip(), "role": m.group("role").strip()})
        else:
            fs_out.append({"slug": slug, "name": slug.replace("-", " ").title(), "role": "Analyst"})
    return fs_out


def get_roster_map() -> dict[str, dict[str, str]]:
    return {c["slug"]: c for c in get_roster()}


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


def _past_reports_summary(*, exclude_id: int | None = None, limit: int = 12) -> list[dict[str, str]]:
    """List of recent done-stage reports, most recent first, used to anchor the
    EIC so it doesn't fabricate a history of prior reports."""
    with Session(engine) as session:
        rows = session.exec(
            select(Report)
            .where(Report.stage == ReportStage.done)
            .order_by(Report.created_at.desc())  # type: ignore[attr-defined]
            .limit(limit + 1)  # +1 to absorb exclusion
        ).all()
    out: list[dict[str, str]] = []
    for r in rows:
        if exclude_id is not None and r.id == exclude_id:
            continue
        out.append({"theme": r.theme, "subtitle": r.subtitle or ""})
        if len(out) >= limit:
            break
    return out


def _tracker(report: Report) -> CostTracker:
    from api import app_settings
    cap = report.budget_cap_usd if report.budget_cap_usd else app_settings.cost_per_report_usd()
    return CostTracker(
        report_cap=cap,
        day_cap=app_settings.cost_per_day_usd(),
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


def _record(report_id: int, wd: Path, result: AgentResult) -> None:
    """After each agent run: persist cost + dedupe-append citations to sources.json."""
    _persist_cost(report_id, result.cost_usd)
    if result.citations:
        _append_citations(wd, result.citations)


MAX_SOURCES = 18  # cap the Sources section so the report stays tidy


def _append_citations(wd: Path, citations: list[Citation]) -> None:
    src = wd / "sources.json"
    existing: list[dict[str, str | None]] = []
    if src.exists():
        try:
            existing = json.loads(src.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = []
    seen = {c.get("url") for c in existing if isinstance(c, dict)}
    for c in citations:
        if c.url and c.url not in seen and len(existing) < MAX_SOURCES:
            existing.append({"url": c.url, "title": c.title, "source": c.source})
            seen.add(c.url)
    src.write_text(json.dumps(existing, indent=2), encoding="utf-8")


def _read_sources(wd: Path) -> list[dict[str, str | None]]:
    sp = wd / "sources.json"
    if not sp.exists():
        return []
    try:
        data = json.loads(sp.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _next_stage(stage: ReportStage) -> ReportStage:
    i = STAGE_ORDER.index(stage)
    return STAGE_ORDER[i + 1]


def _models_for(mode: ReportMode) -> dict[str, str]:
    """Pick model IDs per stage based on report mode. Reads through
    app_settings so dashboard overrides take effect immediately."""
    from api import app_settings
    if mode == ReportMode.fast:
        m = app_settings.model_haiku()
        return {"editor": m, "analyst": m, "data": m}
    return {
        "editor": app_settings.model_opus(),
        "analyst": app_settings.model_sonnet(),
        "data": app_settings.model_sonnet(),
    }


def _persona_path(slug: str) -> Path | None:
    """Find a persona file by slug in either the standing team/ or team/temp/."""
    main = settings.team_dir / f"{slug}.md"
    if main.exists():
        return main
    temp = settings.team_dir / "temp" / f"{slug}.md"
    if temp.exists():
        return temp
    return None


_HEADER_RE = re.compile(r"^#\s+(?P<name>.+?)\s+(?:—|-+)\s+(?P<role>.+)$", re.M)


def _persona_meta(slug: str) -> dict[str, str] | None:
    """Read the #-heading line from a persona file to derive name + role.
    Standing-roster slugs short-circuit through get_roster_map()."""
    roster = get_roster_map()
    if slug in roster:
        return roster[slug]
    p = _persona_path(slug)
    if p is None:
        return None
    text = p.read_text(encoding="utf-8")
    if m := _HEADER_RE.search(text):
        return {"slug": slug, "name": m.group("name").strip(), "role": m.group("role").strip()}
    return {"slug": slug, "name": slug.replace("-", " ").title(), "role": "Specialist"}


def _make_analyst(slug: str, cost: CostTracker, audit, model: str) -> Analyst:  # type: ignore[no-untyped-def]
    p = _persona_path(slug)
    if p is None:
        # Defensive default: fall back to expected standing-team layout so the
        # error surfaces from Anthropic.read() rather than here.
        return Analyst(f"{slug}.md", cost, audit=audit, model=model)
    rel = p.relative_to(settings.team_dir).as_posix()
    return Analyst(rel, cost, audit=audit, model=model)


def _resolved_contributors(report: Report, brief: str) -> list[dict[str, str]]:
    """Resolve which analysts are on this report.
    Priority: explicit team_override on the report -> brief's CONTRIBUTORS
    section + any temp specialists the Recruiter spun up -> full roster."""
    if report.team_override:
        valid = [m for s in report.team_override if (m := _persona_meta(s))]
        if valid:
            return valid

    parsed = parse_brief(brief)
    slugs: list[str] = list(parsed["contributor_slugs"])  # type: ignore[arg-type]
    # Fold in any temp specialists declared in the brief.
    for spec in parsed.get("adhoc_specialists", []) or []:  # type: ignore[union-attr]
        s = spec["slug"] if isinstance(spec, dict) else None
        if s and s not in slugs:
            slugs.append(s)

    valid = [m for s in slugs if (m := _persona_meta(s))]
    return valid or get_roster()


def _run_concurrently(calls: list[Callable[[], AgentResult]]) -> list[AgentResult]:
    """Run a batch of zero-arg callables in parallel threads, return their
    AgentResults in submission order. Used by research + draft stages to
    fan analysts out instead of running them serially."""
    from concurrent.futures import ThreadPoolExecutor

    if not calls:
        return []
    if len(calls) == 1:
        return [calls[0]()]
    with ThreadPoolExecutor(max_workers=len(calls)) as ex:
        futures = [ex.submit(c) for c in calls]
        return [f.result() for f in futures]


def run_stage(report: Report, stage: ReportStage) -> ReportStage:
    """Execute one stage. Returns the next stage to run (or `done`)."""
    if report.id is None:
        raise ValueError("Report has no id")

    wd = working_dir(report.id)
    audit = audit_hook(report.id)
    cost = _tracker(report)
    models = _models_for(report.mode)

    if stage == ReportStage.brief:
        eic = EditorInChief(cost, audit=audit, model=models["editor"])
        result = eic.write_brief(
            report.theme,
            subtitle=report.subtitle,
            available_contributors=get_roster(),
            past_reports=_past_reports_summary(exclude_id=report.id),
        )
        _write(wd / "brief.md", result.text)

        # Resolve contributors now and persist them so /team can compute stats
        # without re-parsing every brief later.
        resolved = _resolved_contributors(report, result.text)
        slugs = [c["slug"] for c in resolved]
        with Session(engine) as session:
            r = session.get(Report, report.id)
            if r:
                r.contributor_slugs = slugs
                if not r.subtitle:
                    sub = parse_brief(result.text).get("subtitle")
                    if sub:
                        r.subtitle = str(sub)
                session.add(r)
                session.commit()
        _record(report.id, wd, result)
        return _next_stage(stage)

    if stage == ReportStage.recruit:
        # Generate persona files for any temp specialists the EIC flagged.
        # If the brief didn't flag any, the stage is a no-op and we move on.
        brief = _read(wd / "brief.md")
        specs = parse_brief(brief).get("adhoc_specialists", []) or []
        temp_dir = settings.team_dir / "temp"
        temp_dir.mkdir(parents=True, exist_ok=True)

        if not specs:
            _write(wd / "recruit.md", "_No ad-hoc specialists requested._\n")
            return _next_stage(stage)

        recruiter = Recruiter(cost, audit=audit, model=models["analyst"])
        log_lines: list[str] = []
        for spec in specs:  # type: ignore[union-attr]
            if not isinstance(spec, dict):
                continue
            slug = spec.get("slug", "")
            request = spec.get("request", "")
            if not slug:
                continue
            try:
                fb = recruiter.propose_specialist(
                    slug=slug, brief_request=request, theme=report.theme,
                )
            except Exception as e:  # noqa: BLE001
                log_lines.append(f"- `{slug}`: failed -- {e}")
                continue
            from api import personas
            from api.models import PersonaStatus
            personas.upsert(slug, fb.text, status=PersonaStatus.temp)
            _record(report.id, wd, fb)
            log_lines.append(f"- `{slug}`: hired ({request})")

        # Refresh contributor_slugs so subsequent stages + /team see the temps.
        with Session(engine) as session:
            r = session.get(Report, report.id)
            if r:
                resolved = _resolved_contributors(r, brief)
                r.contributor_slugs = [c["slug"] for c in resolved]
                session.add(r)
                session.commit()

        _write(wd / "recruit.md", "# Recruit stage\n\n" + "\n".join(log_lines) + "\n")
        return _next_stage(stage)

    if stage == ReportStage.research:
        brief = _read(wd / "brief.md")
        contributors = _resolved_contributors(report, brief)
        analysts = [
            _make_analyst(c["slug"], cost, audit, models["analyst"])
            for c in contributors
        ]
        research_calls: list[Callable[[], AgentResult]] = [
            (lambda a=a: a.research(brief, report.theme, wd, mode=report.mode))  # type: ignore[misc]
            for a in analysts
        ]
        results = _run_concurrently(research_calls)
        for c, result in zip(contributors, results, strict=True):
            _write(wd / f"notes-{c['slug']}.md", result.text)
            _record(report.id, wd, result)
        return _next_stage(stage)

    if stage == ReportStage.charts:
        dc = DataAndCharts(cost, audit=audit, model=models["data"])
        brief = _read(wd / "brief.md")
        all_notes = _concat_notes(wd, _resolved_contributors(report, brief))
        result = dc.build(brief, all_notes, wd / "charts", mode=report.mode)
        _write(wd / "data-section.md", result.text)
        _record(report.id, wd, result)
        return _next_stage(stage)

    if stage == ReportStage.draft:
        brief = _read(wd / "brief.md")
        contributors = _resolved_contributors(report, brief)
        analysts = [
            _make_analyst(c["slug"], cost, audit, models["analyst"])
            for c in contributors
        ]
        notes_per_contributor = [_read(wd / f"notes-{c['slug']}.md") for c in contributors]
        draft_calls: list[Callable[[], AgentResult]] = [
            (lambda a=a, n=n: a.draft(brief, n, report.theme))  # type: ignore[misc]
            for a, n in zip(analysts, notes_per_contributor, strict=True)
        ]
        results = _run_concurrently(draft_calls)
        for c, result in zip(contributors, results, strict=True):
            _write(wd / f"section-{c['slug']}.md", result.text)
            _record(report.id, wd, result)
        return _next_stage(stage)

    if stage == ReportStage.edit:
        eic = EditorInChief(cost, audit=audit, model=models["editor"])
        brief = _read(wd / "brief.md")
        contributors = _resolved_contributors(report, brief)
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
        _record(report.id, wd, result)
        return _next_stage(stage)

    if stage == ReportStage.render:
        edited = _read(wd / "edited.md")
        brief = _read(wd / "brief.md")
        parsed = parse_edited(edited)
        out = wd / "report.pdf"
        contributors_credits = [
            Contributor(EIC_DISPLAY["name"], EIC_DISPLAY["role"]),
            *[Contributor(c["name"], c["role"]) for c in _resolved_contributors(report, brief)],
            Contributor(DC_DISPLAY["name"], DC_DISPLAY["role"]),
        ]
        # Reload report to pick up subtitle written during the brief stage.
        with Session(engine) as session:
            r = session.get(Report, report.id)
            subtitle = r.subtitle if r else None

        from api.citations import attach_inline_citations
        raw_sections = _sections_for_render(parsed, wd)
        cited_sections, ordered_sources = attach_inline_citations(
            raw_sections, _read_sources(wd),
        )

        render_pdf(
            out_path=out,
            title=report.theme.title() if report.theme.islower() else report.theme,
            subtitle=subtitle or parsed.get("opening", "").split("\n")[0][:120],
            date=date.today().isoformat(),
            contributors=contributors_credits,
            sections=cited_sections,
            house_view_top=parsed.get("house_view_top"),
            house_view_bottom=parsed.get("house_view_bottom"),
            read_minutes=8,
            sources=ordered_sources,
        )
        with Session(engine) as session:
            r = session.get(Report, report.id)
            if r:
                r.pdf_path = str(out)
                session.add(r)
                session.commit()

        # Upload to R2 (if configured) so the artifacts survive rebuilds.
        # Best-effort -- a failed upload doesn't fail the report; the local
        # file is still served until the next container rebuild.
        try:
            from api import storage
            storage.upload_artifacts(report.id, wd)
        except Exception:  # noqa: BLE001
            log.exception("R2 upload failed (non-blocking)")

        return _next_stage(stage)

    if stage == ReportStage.feedback:
        eic = EditorInChief(cost, audit=audit, model=models["editor"])
        brief = _read(wd / "brief.md")
        edited = _read(wd / "edited.md")
        edited_sections = {
            s["author"]: s["body"]
            for s in parse_edited(edited).get("sections", [])  # type: ignore[union-attr]
            if isinstance(s, dict)
        }
        log_entries: list[str] = []
        for c in _resolved_contributors(report, brief):
            original = _strip_heading(_read(wd / f"section-{c['slug']}.md"))
            edited_section = edited_sections.get(c["name"], original)
            if not original.strip():
                continue
            try:
                fb = eic.write_feedback(
                    contributor_name=c["name"],
                    contributor_role=c["role"],
                    theme=report.theme,
                    original_draft=original,
                    edited_section=edited_section,
                )
            except Exception as e:  # noqa: BLE001
                # Feedback is non-blocking -- skip on failure rather than fail the stage.
                log_entries.append(f"## {c['name']}\n\n_feedback failed: {e}_\n")
                continue
            _record(report.id, wd, fb)
            persona_path = settings.team_dir / f"{c['slug']}.md"
            try:
                from api.feedback import append_entry
                append_entry(persona_path, body=fb.text, report_id=report.id)
            except Exception as e:  # noqa: BLE001
                log_entries.append(f"## {c['name']}\n\n_failed to append: {e}_\n{fb.text}\n")
                continue
            log_entries.append(f"## {c['name']}\n\n{fb.text}\n")

        _write(wd / "feedback.md", "\n\n---\n\n".join(log_entries) or "_No feedback written._\n")

        # After feedback lands, refresh the recruiter's recommendation queue.
        # Best-effort -- don't fail the report if the review hits a snag.
        try:
            from api.recruiter_review import refresh_recommendations
            refresh_recommendations()
        except Exception:  # noqa: BLE001
            log.exception("recruiter review failed (non-blocking)")
        return _next_stage(stage)

    return ReportStage.done


# ---------- helpers ----------

def _heading(md: str) -> str | None:
    for line in md.splitlines():
        if line.startswith("## "):
            return line[3:].strip()
    return None


def _strip_heading(md: str) -> str:
    """Drop any conversational preamble plus the first `## ...` heading.

    Agents sometimes lead with status lines like 'Both charts rendered. Now
    the section:' before the actual heading. Returning everything after the
    first `## ` line strips both the preamble and the heading so the renderer
    isn't left with two duplicate headings (one from us, one from the agent)."""
    lines = md.splitlines()
    for i, line in enumerate(lines):
        if line.lstrip().startswith("## "):
            return "\n".join(lines[i + 1:]).lstrip("\n")
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

_BRIEF_SECTION_RE = re.compile(r"^# (?P<head>[A-Z][A-Z\- ()]+)\s*\n(?P<body>.+?)(?=\n# |\Z)", re.S | re.M)
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

    # Optional ad-hoc specialists -- the EIC writes one bullet per gap.
    adhoc_specialists: list[dict[str, str]] = []
    a_block = sections.get("AD-HOC SPECIALIST")
    if a_block and "(none)" not in a_block.lower():
        for line in a_block.splitlines():
            m = re.match(r"\s*[-*]\s*`([a-z0-9][a-z0-9-]*)`\s*:\s*(.+)$", line)
            if m:
                adhoc_specialists.append({
                    "slug": m.group(1),
                    "request": m.group(2).strip(),
                })

    subtitle = (sections.get("SUBTITLE") or "").strip().splitlines()[0].strip() if sections.get("SUBTITLE") else ""

    return {
        "angle": sections.get("ANGLE", "").strip(),
        "subtitle": subtitle,
        "questions": sections.get("QUESTIONS", "").strip(),
        "contributor_slugs": contributor_slugs,
        "adhoc_specialists": adhoc_specialists,
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
    # Shared "used" set: each chart renders at most once across the whole PDF
    # even if multiple sections reference the same filename.
    used_charts: set[str] = set()
    if opening := parsed.get("opening"):
        out.append(Section(
            heading="Opening",
            body_md=_inline_charts(str(opening), wd, used=used_charts),
        ))

    sections = parsed.get("sections", [])
    for s in sections:  # type: ignore[union-attr]
        body = _inline_charts(str(s["body"]), wd, used=used_charts)
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
        body = _inline_charts(_strip_heading(data_md), wd, used=used_charts)
        out.append(Section(
            heading="Data & charts",
            body_md=body,
            author=DC_DISPLAY["name"],
            role=DC_DISPLAY["role"],
        ))

    if closing := parsed.get("closing"):
        out.append(Section(
            heading="Closing",
            body_md=_inline_charts(str(closing), wd, used=used_charts),
        ))
    return out


_CHART_TAG_RE = re.compile(r"\[chart:\s*(.+?)\s*\]")


def _inline_charts(body: str, wd: Path, *, used: set[str] | None = None) -> str:
    """Replace `[chart: filename.png]` tags with <figure><img> blocks.

    Pass a shared `used` set across multiple calls (e.g. one per section) to
    guarantee each chart renders at most once across the full document. If the
    referenced chart is missing or already used, fall back to the next available
    chart in the directory; surplus refs are stripped silently."""
    charts_dir = (wd / "charts").resolve()
    available: list[Path] = sorted(charts_dir.glob("*.png")) if charts_dir.exists() else []
    if used is None:
        used = set()

    def repl(m: re.Match[str]) -> str:
        fname = m.group(1).strip()
        path = (charts_dir / fname)
        if path.exists() and fname not in used:
            used.add(fname)
            return f'<figure><img src="{path.as_uri()}" alt="{fname}"></figure>'
        # Substitute the next unused chart in the directory.
        for cand in available:
            if cand.name not in used:
                used.add(cand.name)
                return f'<figure><img src="{cand.as_uri()}" alt="{cand.name}"></figure>'
        # No charts left. Drop the reference quietly.
        return ""

    return _CHART_TAG_RE.sub(repl, body)
