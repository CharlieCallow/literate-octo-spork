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
from api.agents.auditor import Auditor
from api.agents.base import AgentResult, Citation
from api.agents.charts import DataAndCharts
from api.agents.cost import CostTracker
from api.agents.editor import EditorInChief
from api.agents.recruiter import Recruiter
from api.agents.redteam import RedTeam
from api.db import engine
from api.models import AuditLog, Report, ReportMode, ReportStage
from api.render.pdf import Contributor, Section, render_pdf
from api.settings import settings
from api.workflow.audit import audit_hook

log = logging.getLogger("workflow")

# Concurrency + timeout caps for the parallel-analyst stages. Anthropic
# enforces both per-minute token caps and concurrent-request limits, so
# spawning 8 analysts at once tends to make most of them queue inside
# the SDK anyway. Cap to 4 in flight; abandon any one call past the
# per-call deadline so the rest of the stage doesn't get held hostage
# by one slow contributor.
_PARALLEL_ANALYST_CAP = 4
# Draft is a single LLM call per analyst (no tools); 4 minutes is
# extremely generous even with one Anthropic SDK retry.
_DRAFT_CALL_TIMEOUT_S = 240.0
# Research is tool-using; allow more headroom on deep mode where the
# analyst may iterate through 10 tool calls.
_RESEARCH_CALL_TIMEOUT_S: dict[ReportMode, float] = {
    ReportMode.test:     90.0,
    ReportMode.fast:     180.0,
    ReportMode.standard: 360.0,
    ReportMode.deep:     600.0,
}


STAGE_ORDER: list[ReportStage] = [
    ReportStage.brief,
    ReportStage.recruit,
    ReportStage.research,
    ReportStage.charts,
    ReportStage.draft,
    ReportStage.rebuttal,
    ReportStage.redteam,
    ReportStage.edit,
    ReportStage.audit,
    ReportStage.render,
    ReportStage.feedback,
    ReportStage.housekeeping,
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
_NON_ROSTER_SLUGS = {"editor-in-chief", "data-and-charts", "scout", "recruiter", "devils-advocate"}

EIC_DISPLAY = {"name": "Margaux Devlin", "role": "Editor-in-Chief"}
DC_DISPLAY  = {"name": "Tomás Reyes",    "role": "Data & Charts"}
RT_DISPLAY  = {"name": "Saoirse Mok",    "role": "Devil's Advocate"}


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
    EIC so it doesn't fabricate a history of prior reports.

    Excludes `is_test` rows -- test-mode smoke runs aren't real reports
    and shouldn't shape the Scout's view of what the firm has covered."""
    with Session(engine) as session:
        rows = session.exec(
            select(Report)
            .where(Report.stage == ReportStage.done)
            .where(Report.is_test == False)  # noqa: E712
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


def _append_tool_outputs(wd: Path, agent_slug: str, result: AgentResult) -> None:
    """Append a JSONL ledger of every tool call this agent ran.

    Read by the audit stage to ground numerical claims in the final prose
    against what the tools actually returned. One line per call:
    {agent, tool, input, output}."""
    if not result.tool_outputs:
        return
    ledger = wd / "tool-outputs.jsonl"
    with ledger.open("a", encoding="utf-8") as f:
        for to in result.tool_outputs:
            f.write(json.dumps({
                "agent": agent_slug,
                "tool": to.tool,
                "input": to.input,
                "output": to.output,
            }) + "\n")


# Conviction tags the analyst writes inline ({c1}..{c5}). They're an editorial
# signal for the EIC -- drop them from the rendered prose. Eat any preceding
# spaces (but not newlines) so we don't leave a stray space before the next
# punctuation mark.
_CONVICTION_TAG_RE = re.compile(r" *\{c[1-5]\}")


def _strip_conviction_tags(md: str) -> str:
    return _CONVICTION_TAG_RE.sub("", md)


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


# Stages that the test mode skips outright. Each is non-essential for a
# pipeline smoke run -- the resulting report is shorter and uglier but
# the brief / research / draft / edit / render path still produces a PDF.
# Cost target on test mode is sub-$0.05; with these stages omitted we
# typically land around $0.02.
TEST_MODE_SKIP_STAGES: frozenset[ReportStage] = frozenset({
    ReportStage.recruit,       # no ad-hoc specialists
    ReportStage.charts,        # no chart generation
    ReportStage.rebuttal,      # no cross-analyst critique
    ReportStage.redteam,       # no devil's advocate pass
    ReportStage.audit,         # no post-edit numerical audit
    ReportStage.feedback,      # no persona-feedback writes
    ReportStage.housekeeping,  # no house view / tagger / threader / voice stats
})

# Test mode caps the analyst roster so a 6-person team doesn't fan out
# into 6 parallel research calls. Two voices is the minimum that still
# exercises the multi-contributor path (sections + the EIC's coherence
# pass without rebuttal).
TEST_MODE_CONTRIBUTOR_CAP = 2


def _next_stage(stage: ReportStage, mode: ReportMode = ReportMode.standard) -> ReportStage:
    """Next stage in the workflow. For test mode, skips ahead past any
    stage in TEST_MODE_SKIP_STAGES so the cheap pipeline doesn't pay
    for stages it doesn't run."""
    i = STAGE_ORDER.index(stage)
    nxt = STAGE_ORDER[i + 1]
    if mode == ReportMode.test:
        while nxt in TEST_MODE_SKIP_STAGES:
            i = STAGE_ORDER.index(nxt)
            nxt = STAGE_ORDER[i + 1]
    return nxt


def _models_for(mode: ReportMode) -> dict[str, str]:
    """Pick model IDs per stage based on report mode. Reads through
    app_settings so dashboard overrides take effect immediately."""
    from api import app_settings
    if mode in (ReportMode.test, ReportMode.fast):
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
    section + any temp specialists the Recruiter spun up -> full roster.

    Test mode caps the result to TEST_MODE_CONTRIBUTOR_CAP so a 6-person
    roster doesn't fan out into 6 parallel research calls."""
    contributors: list[dict[str, str]] | None = None
    if report.team_override:
        valid = [m for s in report.team_override if (m := _persona_meta(s))]
        if valid:
            contributors = valid

    if contributors is None:
        parsed = parse_brief(brief)
        slugs: list[str] = list(parsed["contributor_slugs"])  # type: ignore[arg-type]
        # Fold in any temp specialists declared in the brief.
        for spec in parsed.get("adhoc_specialists", []) or []:  # type: ignore[union-attr]
            s = spec["slug"] if isinstance(spec, dict) else None
            if s and s not in slugs:
                slugs.append(s)
        valid = [m for s in slugs if (m := _persona_meta(s))]
        contributors = valid or get_roster()

    if report.mode == ReportMode.test:
        contributors = contributors[:TEST_MODE_CONTRIBUTOR_CAP]
    return contributors


def _run_concurrently(
    calls: list[Callable[[], AgentResult]],
    *,
    per_call_timeout_s: float | None = None,
    max_workers: int | None = None,
    labels: list[str] | None = None,
) -> list[AgentResult]:
    """Run a batch of zero-arg callables in parallel threads, return their
    AgentResults in submission order.

    `per_call_timeout_s`: max wall-clock to wait for any single call. A
    call that exceeds it is abandoned (the future is left to drain into
    the executor's shutdown) and replaced with an error placeholder so
    the slow contributor doesn't block the whole stage. The most common
    cause is one analyst hitting Anthropic rate-limit retries while the
    others have already finished.

    `max_workers`: cap on concurrent threads. Anthropic enforces both
    per-minute token caps and concurrent-request limits; spawning eight
    analysts at once means most of them queue inside the SDK anyway.

    `labels`: optional human-readable name per call for log lines."""
    from concurrent.futures import ThreadPoolExecutor
    from concurrent.futures import TimeoutError as FTimeout

    if not calls:
        return []
    workers = max_workers or len(calls)
    workers = max(1, min(workers, len(calls)))

    if labels is None:
        labels = [f"call[{i}]" for i in range(len(calls))]
    if len(labels) != len(calls):
        labels = [f"call[{i}]" for i in range(len(calls))]

    # Single-call shortcut: no need to spin a pool.
    if len(calls) == 1:
        try:
            return [calls[0]()]
        except Exception as e:  # noqa: BLE001
            log.exception("%s failed (non-blocking)", labels[0])
            return [AgentResult(text=f"[{labels[0]} failed: {e}]", cost_usd=0.0)]

    results: list[AgentResult] = [
        AgentResult(text="", cost_usd=0.0) for _ in calls
    ]
    # Don't auto-shutdown(wait=True) the pool on context exit -- if a
    # thread is hung waiting on Anthropic, the implicit wait would
    # re-introduce the original deadlock. We pass wait=False at end.
    ex = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = {ex.submit(c): i for i, c in enumerate(calls)}
        for fut, i in list(futures.items()):
            label = labels[i]
            try:
                results[i] = fut.result(timeout=per_call_timeout_s)
            except FTimeout:
                log.warning(
                    "%s exceeded %.0fs timeout; abandoning so the stage can move on",
                    label, per_call_timeout_s or 0.0,
                )
                results[i] = AgentResult(
                    text=f"[{label} timed out after {per_call_timeout_s:.0f}s]",
                    cost_usd=0.0,
                )
            except Exception as e:  # noqa: BLE001
                log.exception("%s failed", label)
                results[i] = AgentResult(
                    text=f"[{label} failed: {e}]", cost_usd=0.0,
                )
    finally:
        # Don't wait for hung threads -- the worker process owns them and
        # they'll get reaped if the process restarts. We've already moved
        # on with placeholders.
        ex.shutdown(wait=False, cancel_futures=True)
    return results


def run_stage(report: Report, stage: ReportStage) -> ReportStage:
    """Execute one stage. Returns the next stage to run (or `done`)."""
    if report.id is None:
        raise ValueError("Report has no id")

    # Test-mode skip: a few stages are non-essential for a smoke run
    # (charts, rebuttal, redteam, audit, feedback, housekeeping). Bypass
    # them entirely rather than letting each stage's own no-op path
    # spend any LLM tokens.
    if report.mode == ReportMode.test and stage in TEST_MODE_SKIP_STAGES:
        log.info("test-mode skip: stage=%s report=%s", stage, report.id)
        return _next_stage(stage, report.mode)

    wd = working_dir(report.id)
    # Hydrate the working dir from R2 before any stage reads files.
    # Railway rebuilds wipe the local container fs; without this, a
    # re-run from `edit` (or any stage after the first) finds no
    # section-*.md / redteam.md / rebuttals.md inputs and produces an
    # empty REVISED SECTIONS block, rendering as a cover-only PDF. Skip
    # for `brief` (the first stage) since there's nothing to hydrate.
    # No-op when R2 isn't configured or all files are already local.
    if stage != ReportStage.brief:
        try:
            from api import storage
            storage.hydrate_working_dir(report.id, wd)
        except Exception:  # noqa: BLE001
            log.exception("R2 hydrate failed (non-blocking)")
    audit = audit_hook(report.id)
    cost = _tracker(report)
    models = _models_for(report.mode)

    if stage == ReportStage.brief:
        from api import house_view as house_view_mod
        from api.agents.primer import Primer

        # Pre-brief data primer. Cheap Haiku scan that anchors the brief to
        # what the tape actually says today instead of training-time priors.
        # Best-effort: if it fails, the EIC just writes the brief without it.
        primer_text = ""
        try:
            primer_agent = Primer(cost, audit=audit)
            pr = primer_agent.primer(report.theme, subtitle=report.subtitle)
            primer_text = pr.text
            _write(wd / "primer.md", primer_text)
            _record(report.id, wd, pr)
            _append_tool_outputs(wd, "primer", pr)
        except Exception:  # noqa: BLE001
            log.exception("primer stage failed (non-blocking)")

        eic = EditorInChief(cost, audit=audit, model=models["editor"])
        # Per-analyst calibration anchors the EIC's contributor weighting
        # in actual track record rather than persona vibes. Skipped on
        # test mode -- smoke runs don't extract calls anyway.
        roster = get_roster()
        cal_lines: dict[str, str] = {}
        if report.mode != ReportMode.test:
            try:
                from api import calibration as cal_mod
                all_cal = cal_mod.for_all()
                for c in roster:
                    if c["slug"] in all_cal:
                        line = cal_mod.format_for_brief(all_cal[c["slug"]], c["name"])
                        if line:
                            cal_lines[c["slug"]] = line
            except Exception:  # noqa: BLE001
                log.exception("calibration lookup failed (non-blocking)")
        result = eic.write_brief(
            report.theme,
            subtitle=report.subtitle,
            available_contributors=roster,
            past_reports=_past_reports_summary(exclude_id=report.id),
            house_view=house_view_mod.get(),
            primer=primer_text or None,
            calibration=cal_lines or None,
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
        return _next_stage(stage, report.mode)

    if stage == ReportStage.recruit:
        # Generate persona files for any temp specialists the EIC flagged.
        # If the brief didn't flag any, the stage is a no-op and we move on.
        brief = _read(wd / "brief.md")
        specs = parse_brief(brief).get("adhoc_specialists", []) or []
        temp_dir = settings.team_dir / "temp"
        temp_dir.mkdir(parents=True, exist_ok=True)

        if not specs:
            _write(wd / "recruit.md", "_No ad-hoc specialists requested._\n")
            return _next_stage(stage, report.mode)

        recruiter = Recruiter(cost, audit=audit, model=models["analyst"])
        log_lines: list[str] = []
        # Track slugs that should end up on the report for stages downstream
        # of recruit. Starts with the brief's standing contributors; the
        # registry / fresh-hire branches each add their resolved slug.
        from api import personas, specialist_registry
        from api.models import PersonaStatus
        parsed_brief = parse_brief(brief)
        resolved_slugs: list[str] = list(parsed_brief["contributor_slugs"])  # type: ignore[arg-type]

        for spec in specs:  # type: ignore[union-attr]
            if not isinstance(spec, dict):
                continue
            slug = spec.get("slug", "")
            request = spec.get("request", "")
            if not slug:
                continue

            # Specialist registry: if a previous report already hired a
            # specialist for the same ground, reuse them. No fresh LLM call
            # needed; archived personas get rehired into temp status.
            existing = specialist_registry.find_match(request)
            if existing is not None:
                if existing.status == PersonaStatus.archived:
                    personas.set_status(existing.slug, PersonaStatus.temp)
                if existing.slug not in resolved_slugs:
                    resolved_slugs.append(existing.slug)
                log_lines.append(
                    f"- `{existing.slug}`: reused from registry "
                    f"({existing.name} — {existing.role})"
                )
                continue

            try:
                fb = recruiter.propose_specialist(
                    slug=slug, brief_request=request, theme=report.theme,
                )
            except Exception as e:  # noqa: BLE001
                log_lines.append(f"- `{slug}`: failed -- {e}")
                continue
            personas.upsert(slug, fb.text, status=PersonaStatus.temp)
            _record(report.id, wd, fb)
            if slug not in resolved_slugs:
                resolved_slugs.append(slug)
            log_lines.append(f"- `{slug}`: hired ({request})")

        # Pin the resolved slug list to team_override so registry rewrites
        # propagate through _resolved_contributors (which would otherwise
        # re-parse brief.md and lose them).
        with Session(engine) as session:
            r = session.get(Report, report.id)
            if r:
                r.team_override = resolved_slugs
                resolved = _resolved_contributors(r, brief)
                r.contributor_slugs = [c["slug"] for c in resolved]
                session.add(r)
                session.commit()

        _write(wd / "recruit.md", "# Recruit stage\n\n" + "\n".join(log_lines) + "\n")
        return _next_stage(stage, report.mode)

    if stage == ReportStage.research:
        brief = _read(wd / "brief.md")
        contributors = _resolved_contributors(report, brief)
        analysts = [
            _make_analyst(c["slug"], cost, audit, models["analyst"])
            for c in contributors
        ]
        from api import uploads as uploads_mod
        has_uploads = bool(uploads_mod.list_documents(report.id))
        research_calls: list[Callable[[], AgentResult]] = [
            (lambda a=a: a.research(  # type: ignore[misc]
                brief, report.theme, wd,
                mode=report.mode,
                report_id=report.id,
                has_uploads=has_uploads,
            ))
            for a in analysts
        ]
        # Research is tool-using and can legitimately run several minutes
        # per analyst. Cap per-call so one slow contributor doesn't hold
        # up the rest, and cap concurrency so we don't slam Anthropic.
        results = _run_concurrently(
            research_calls,
            per_call_timeout_s=_RESEARCH_CALL_TIMEOUT_S[report.mode],
            max_workers=_PARALLEL_ANALYST_CAP,
            labels=[f"research:{c['slug']}" for c in contributors],
        )
        for c, result in zip(contributors, results, strict=True):
            _write(wd / f"notes-{c['slug']}.md", result.text)
            _record(report.id, wd, result)
            _append_tool_outputs(wd, c["slug"], result)

        # Pre-draft self-audit + coverage check both run extra LLM calls
        # at research-stage tail. Skip them in test mode -- the goal there
        # is a cheap pipeline smoke, not a polished report.
        skip_extras = report.mode == ReportMode.test

        # Pre-draft self-audit. Each analyst's notes get a Haiku grounding
        # pass against their slice of the tool ledger -- ungrounded numbers
        # are qualified or struck before the draft stage reads them.
        # Best-effort: if the audit call fails, leave the original notes.
        ledger = _read(wd / "tool-outputs.jsonl")
        if ledger.strip() and not skip_extras:
            try:
                pre_auditor = Auditor(cost, audit=audit)
                for c in contributors:
                    notes_path = wd / f"notes-{c['slug']}.md"
                    notes_md = _read(notes_path)
                    if not notes_md.strip():
                        continue
                    try:
                        pa_result = pre_auditor.pre_draft_audit(
                            agent_slug=c["slug"], notes_md=notes_md,
                            tool_outputs_jsonl=ledger,
                        )
                        if pa_result.text.strip():
                            _write(notes_path, pa_result.text)
                            _record(report.id, wd, pa_result)
                    except Exception:  # noqa: BLE001
                        log.exception("pre-draft audit failed for %s", c["slug"])
            except Exception:  # noqa: BLE001
                log.exception("pre-draft audit init failed (non-blocking)")

        # Brief-coverage check. Map each numbered # QUESTIONS bullet to its
        # status in the notes; gaps surface to the EIC at edit time.
        # Best-effort: a failure here just means the EIC works without
        # explicit coverage tagging.
        if not skip_extras:
            try:
                from api.agents.coverage import Coverage
                coverage = Coverage(cost, audit=audit)
                all_notes = _concat_notes(wd, contributors)
                cov_result = coverage.check(brief=brief, notes=all_notes)
                _write(wd / "coverage.md", cov_result.text)
                _record(report.id, wd, cov_result)
            except Exception:  # noqa: BLE001
                log.exception("coverage check failed (non-blocking)")

        return _next_stage(stage, report.mode)

    if stage == ReportStage.charts:
        dc = DataAndCharts(cost, audit=audit, model=models["data"])
        brief = _read(wd / "brief.md")
        all_notes = _concat_notes(wd, _resolved_contributors(report, brief))
        result = dc.build(brief, all_notes, wd / "charts", mode=report.mode)
        _write(wd / "data-section.md", result.text)
        _record(report.id, wd, result)
        _append_tool_outputs(wd, "data-and-charts", result)
        return _next_stage(stage, report.mode)

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
        # Draft is a single LLM call per analyst (no tools) so it should
        # never legitimately need more than 4 minutes. Cap per-call so
        # one stuck contributor doesn't take the whole stage past the
        # watchdog deadline.
        results = _run_concurrently(
            draft_calls,
            per_call_timeout_s=_DRAFT_CALL_TIMEOUT_S,
            max_workers=_PARALLEL_ANALYST_CAP,
            labels=[f"draft:{c['slug']}" for c in contributors],
        )
        for c, result in zip(contributors, results, strict=True):
            _write(wd / f"section-{c['slug']}.md", result.text)
            _record(report.id, wd, result)
        return _next_stage(stage, report.mode)

    if stage == ReportStage.rebuttal:
        # Each analyst sees the peer drafts and writes a one-paragraph
        # reaction. The EIC consumes these at edit time as raw material for
        # the DISAGREEMENT block. Best-effort: per-analyst failures are
        # logged and dropped so a single rebuttal failure doesn't stall
        # the report.
        brief = _read(wd / "brief.md")
        contributors = _resolved_contributors(report, brief)
        # Skip if there's only one analyst -- rebutting yourself is a
        # nonsense call. Same if no drafts landed.
        sections_by_slug: dict[str, dict[str, str]] = {}
        for c in contributors:
            body_md = _read(wd / f"section-{c['slug']}.md")
            if not body_md.strip():
                continue
            sections_by_slug[c["slug"]] = {
                "author": c["name"],
                "role": c["role"],
                "body": _strip_heading(body_md),
            }
        if len(sections_by_slug) < 2:
            _write(wd / "rebuttals.md", "_Rebuttal stage skipped: fewer than two analyst sections._\n")
            return _next_stage(stage, report.mode)

        analysts_by_slug = {
            c["slug"]: _make_analyst(c["slug"], cost, audit, models["analyst"])
            for c in contributors if c["slug"] in sections_by_slug
        }

        def _rebut_call(slug: str):  # type: ignore[no-untyped-def]
            me = sections_by_slug[slug]
            peers = [
                {"author": v["author"], "role": v["role"], "body": v["body"]}
                for k, v in sections_by_slug.items() if k != slug
            ]
            return lambda: analysts_by_slug[slug].rebut(
                brief=brief, my_section=me["body"],
                peer_sections=peers, theme=report.theme,
            )

        slugs_in_order = [c["slug"] for c in contributors if c["slug"] in sections_by_slug]
        rebuttal_results = _run_concurrently(
            [_rebut_call(s) for s in slugs_in_order],
            per_call_timeout_s=_DRAFT_CALL_TIMEOUT_S,
            max_workers=_PARALLEL_ANALYST_CAP,
            labels=[f"rebuttal:{s}" for s in slugs_in_order],
        )

        rebuttal_lines: list[str] = ["# Rebuttals\n"]
        for slug, result in zip(slugs_in_order, rebuttal_results, strict=True):
            text = (result.text or "").strip()
            if not text or "(no substantive disagreement)" in text.lower():
                continue
            author = sections_by_slug[slug]["author"]
            role = sections_by_slug[slug]["role"]
            rebuttal_lines.append(f"## {author} — {role}\n\n{text}\n")
            _record(report.id, wd, result)
        _write(
            wd / "rebuttals.md",
            "\n".join(rebuttal_lines)
            if len(rebuttal_lines) > 1
            else "_All analysts agree -- no rebuttals._\n",
        )

        # Cross-analyst differentiation check. Two sections covering the
        # same beat with the same evidence is the failure mode -- voices
        # blur into one. Compute pairwise lexical overlap and surface
        # high-overlap pairs to the editor so the edit stage can either
        # compress one section or push them onto distinct axes.
        overlap_pairs = _compute_section_overlap(sections_by_slug)
        if overlap_pairs:
            lines = ["# Differentiation flag\n"]
            for a, b, score in overlap_pairs:
                lines.append(
                    f"- {sections_by_slug[a]['author']} vs "
                    f"{sections_by_slug[b]['author']}: "
                    f"{score:.0%} content overlap"
                )
            _write(wd / "differentiation.md", "\n".join(lines) + "\n")
        else:
            _write(wd / "differentiation.md", "")
        return _next_stage(stage, report.mode)

    if stage == ReportStage.redteam:
        # Saoirse reads the drafts and writes the strongest counter-thesis.
        # Best-effort: if she fails, log it and move on; the EIC will still
        # produce a report, just without the bear case integrated.
        brief = _read(wd / "brief.md")
        contributors = _resolved_contributors(report, brief)
        sections: list[dict[str, str]] = []
        for c in contributors:
            body_md = _read(wd / f"section-{c['slug']}.md")
            if body_md.strip():
                sections.append({
                    "heading": _heading(body_md) or c["role"],
                    "body": _strip_heading(body_md),
                    "author": c["name"],
                    "role": c["role"],
                })
        chart_summary = _list_charts(wd / "charts")
        try:
            rt = RedTeam(cost, audit=audit, model=models["analyst"])
            result = rt.critique(brief=brief, sections=sections, chart_summary=chart_summary)
            _write(wd / "redteam.md", result.text)
            _record(report.id, wd, result)
        except Exception as e:  # noqa: BLE001
            log.exception("redteam stage failed (non-blocking)")
            _write(wd / "redteam.md", f"_red-team pass failed: {e}_\n")
        return _next_stage(stage, report.mode)

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
        bear_note = _read(wd / "redteam.md").strip()

        # Degraded-section fold-in. If any contributor's section file is
        # missing or a stub (data pull failed, draft timed out, etc.), the
        # EIC is told which beats are missing + their brief assignment so
        # they can be absorbed into a neighbouring section's prose rather
        # than rendered as a "Section pulled" recovery note. Audit stage
        # is the safety net if the EIC skips the directive.
        stub_slugs = _stub_contributor_slugs(wd, contributors)
        degraded_sections: list[dict[str, str]] = []
        if stub_slugs:
            brief_contributor_lines: dict[str, str] = {}
            # Pull each missing slug's one-line brief from the CONTRIBUTORS
            # block so the EIC has the structural points to fold in.
            for line in brief.splitlines():
                m = re.match(
                    r"\s*[-*]\s*`([a-z0-9][a-z0-9-]*)`\s*:\s*(.+)$", line,
                )
                if m:
                    brief_contributor_lines[m.group(1)] = m.group(2).strip()
            slug_to_meta = {c["slug"]: c for c in contributors}
            for slug in stub_slugs:
                meta = slug_to_meta.get(slug, {})
                degraded_sections.append({
                    "slug": slug,
                    "name": meta.get("name", slug),
                    "role": meta.get("role", ""),
                    "assignment": brief_contributor_lines.get(slug, "(no brief assignment recorded)"),
                })
            log.warning(
                "edit: %d contributor section(s) missing/stub for report %s -- folding into neighbours: %s",
                len(stub_slugs), report.id, ", ".join(stub_slugs),
            )

        # Cross-analyst rebuttals -- raw material for the DISAGREEMENT block.
        rebuttals = _parse_rebuttals(_read(wd / "rebuttals.md"))

        # Source-diversity flag. Computed off the running sources.json so the
        # editor sees the same publisher distribution the renderer will end
        # up persisting on the report row.
        from api.citations import domain_distribution
        top_dom, dom_share = domain_distribution(_read_sources(wd))
        source_diversity: dict[str, object] | None = None
        if top_dom and dom_share >= 0.40:
            source_diversity = {"top_domain": top_dom, "top_share": dom_share}

        # Brief-coverage gaps -- questions the research stage didn't answer.
        from api.agents.coverage import gaps as _coverage_gaps
        coverage_gaps_list = _coverage_gaps(brief, _read(wd / "coverage.md"))

        # Differentiation flag: pairs of sections that overlap > 40% on
        # content trigrams. The editor compresses one or pushes them onto
        # distinct axes so two analysts aren't writing the same paragraph
        # in different voices.
        differentiation_md = _read(wd / "differentiation.md").strip() or None

        result = eic.edit(
            brief=brief, sections=sections,
            chart_summary=chart_summary,
            bear_note=bear_note or None,
            rebuttals=rebuttals or None,
            source_diversity=source_diversity,
            coverage_gaps=coverage_gaps_list or None,
            differentiation=differentiation_md,
            degraded_sections=degraded_sections or None,
        )
        _write(wd / "edited.md", result.text)
        _record(report.id, wd, result)
        # Truncation guard. If the editor hit max_tokens, the rendered
        # PDF will cut off mid-block (typically inside DISAGREEMENT,
        # which lands after the long REVISED SECTIONS). Flag it on the
        # report row so the dashboard shows a warning instead of
        # rendering broken prose silently.
        if result.stop_reason == "max_tokens":
            log.warning(
                "edit stage hit max_tokens for report %s -- prose may be truncated",
                report.id,
            )
            with Session(engine) as session:
                r = session.get(Report, report.id)
                if r:
                    note = (
                        "Editor output hit max_tokens cap and may be "
                        "truncated mid-block. Try /resume from edit; "
                        "if it persists, fewer contributors will fit."
                    )
                    r.error = (r.error + "\n\n" + note) if r.error else note
                    session.add(r)
                    session.commit()
        return _next_stage(stage, report.mode)

    if stage == ReportStage.audit:
        # Two passes here:
        # 1. Strip failure markers leaked from earlier stages (timeouts,
        #    halted iters, tool errors). A reader should never see
        #    "[agent halted: max iterations reached]" or "the upstream
        #    data pull failed" in the rendered PDF -- that's a pipeline
        #    bug surfacing as prose. Stripping is a hard gate; we also
        #    flag on report.error so the dashboard shows it.
        # 2. Ground every numerical claim against the tool-output ledger.
        edited = _read(wd / "edited.md")
        # Also clean the data-section.md, which bypasses the edit stage and
        # can carry chart-agent failure markers straight to render.
        data_section = _read(wd / "data-section.md")
        cleaned, failures = _scrub_failure_markers(edited)
        # Hard gate: any REVISED SECTIONS block whose body is still a
        # recovery stub after the EIC's degraded-section fold-in directive
        # ran (or the directive failed) gets removed entirely. We never
        # ship a section header followed by an editor-handwave paragraph.
        cleaned, dropped_headings = _drop_stub_revised_sections(cleaned)
        cleaned_data, data_failures = _scrub_failure_markers(data_section)
        all_failures = failures + data_failures
        if dropped_headings:
            log.warning(
                "audit: dropped %d stub section(s) from prose for report %s: %s",
                len(dropped_headings), report.id, "; ".join(dropped_headings),
            )
            with Session(engine) as session:
                r = session.get(Report, report.id)
                if r:
                    note = (
                        "Audit gate dropped stub section(s) the EIC failed to "
                        "fold into a neighbour: " + "; ".join(dropped_headings)
                    )
                    r.error = (r.error + "\n\n" + note) if r.error else note
                    session.add(r)
                    session.commit()
        if all_failures:
            log.warning(
                "audit: stripped %d failure marker(s) from prose for report %s",
                len(all_failures), report.id,
            )
            with Session(engine) as session:
                r = session.get(Report, report.id)
                if r:
                    note = (
                        "Audit stage stripped pipeline failure markers from "
                        "the prose before render: "
                        + "; ".join(all_failures[:5])
                        + (" …" if len(all_failures) > 5 else "")
                    )
                    r.error = (r.error + "\n\n" + note) if r.error else note
                    session.add(r)
                    session.commit()
            if cleaned != edited:
                _write(wd / "edited.md", cleaned)
                edited = cleaned
            if cleaned_data != data_section:
                _write(wd / "data-section.md", cleaned_data)

        ledger = _read(wd / "tool-outputs.jsonl")
        if not edited.strip() or not ledger.strip():
            return _next_stage(stage, report.mode)
        try:
            auditor = Auditor(cost, audit=audit)
            result = auditor.review(edited=edited, tool_outputs_jsonl=ledger)
            _write(wd / "audited.md", result.text)
            _record(report.id, wd, result)
        except Exception as e:  # noqa: BLE001
            log.exception("audit stage failed (non-blocking)")
            _write(wd / "audited.md", "")  # empty marker -> render falls back to edited.md
            _ = e
        return _next_stage(stage, report.mode)

    if stage == ReportStage.render:
        # Prefer audited prose if the audit stage produced one. Strip the
        # analyst's conviction tags ({c1}..{c5}) before parsing so they don't
        # appear in the rendered PDF.
        audited = _read(wd / "audited.md").strip()
        edited = audited or _read(wd / "edited.md")
        edited = _strip_conviction_tags(edited)
        brief = _read(wd / "brief.md")
        parsed = parse_edited(edited)

        # Extract structured calls before the cover renders so they can show
        # up in the position tracker. Idempotent: skip if this report already
        # has rows (rerunning render after a fix shouldn't dupe positions).
        # Test mode skips this LLM call entirely -- no positions on a smoke run.
        from api import calls as calls_mod
        if report.mode != ReportMode.test and not calls_mod.has_calls_for(report.id):
            try:
                contributors_for_calls = _resolved_contributors(report, brief)
                extractor = calls_mod.CallExtractor(cost, audit=audit)
                redteam_text = _read(wd / "redteam.md").strip()
                if redteam_text.lower().startswith("_red-team pass failed"):
                    redteam_text = ""
                ext_result = extractor.extract(
                    prose=edited,
                    contributor_slugs=[c["slug"] for c in contributors_for_calls],
                    redteam_prose=redteam_text or None,
                )
                parsed_calls = calls_mod.parse_extracted(ext_result.text)
                # Saoirse contributes via the redteam pass, not the roster --
                # whitelist her slug so her "trade we're missing" call survives.
                slug_set = {c["slug"] for c in contributors_for_calls}
                slug_set.add("devils-advocate")
                clean = [c for c in parsed_calls if c["contributor_slug"] in slug_set]
                calls_mod.persist(report.id, clean)
                _record(report.id, wd, ext_result)
            except Exception:  # noqa: BLE001
                log.exception("inline call extraction failed (non-blocking)")
        out = wd / "report.pdf"
        contributors_credits = [
            Contributor(EIC_DISPLAY["name"], EIC_DISPLAY["role"]),
            *[Contributor(c["name"], c["role"]) for c in _resolved_contributors(report, brief)],
            Contributor(DC_DISPLAY["name"], DC_DISPLAY["role"]),
        ]
        # Roster invariant: if Saoirse contributed (the BEAR CASE block is
        # non-empty, or redteam.md has substantive prose), she earns the
        # byline. The edited prose names her ("Saoirse's strongest
        # objection"); a reader hitting that name shouldn't have to wonder
        # who she is.
        bear_text = (parsed.get("bear_case") or "").strip()
        redteam_text = _read(wd / "redteam.md").strip()
        rt_substantive = (
            bool(bear_text)
            or (
                bool(redteam_text)
                and not redteam_text.lower().startswith("_red-team pass failed")
            )
        )
        if rt_substantive:
            contributors_credits.append(
                Contributor(RT_DISPLAY["name"], RT_DISPLAY["role"]),
            )
        # Reload report to pick up subtitle written during the brief stage.
        with Session(engine) as session:
            r = session.get(Report, report.id)
            subtitle = r.subtitle if r else None

        from api.citations import attach_inline_citations, domain_distribution
        # Chart-vs-title audit. Quarantine charts whose title makes a claim
        # the underlying data can't support (categorical title with date
        # index, "X vs Y" with one series, etc.). Quarantined PNGs get a
        # `.suspect` suffix so `_inline_charts` substitutes the next
        # available chart for any prose reference. Best-effort.
        try:
            from api.render.chart_audit import audit_charts_dir, format_report
            failed_charts = audit_charts_dir(wd / "charts")
            if failed_charts:
                note = format_report(failed_charts)
                with Session(engine) as session:
                    r = session.get(Report, report.id)
                    if r and note:
                        r.error = (r.error + "\n\n" + note) if r.error else note
                        session.add(r)
                        session.commit()
        except Exception:  # noqa: BLE001
            log.exception("chart audit failed (non-blocking)")
        raw_sections = _sections_for_render(parsed, wd)

        # Glossary appendix: a Haiku pass over the edited prose tags
        # the technical terms a generalist reader wouldn't know. Best-
        # effort -- a failure here just means no appendix. Skipped on
        # test mode (smoke runs don't get the polish layer). Stored as
        # a stand-alone string so the PDF template can place it at the
        # end (after disagreement / bear case / bottom line, before
        # sources) rather than inlining as another contributor section.
        glossary_md: str | None = None
        if report.mode != ReportMode.test:
            try:
                from api.agents.glossary import Glossary, is_empty
                glossary = Glossary(cost, audit=audit)
                # Pass the equity layer's ticker -> issuer resolutions so the
                # glossary inherits them rather than running its own free
                # lookup (which is how "TLN = Talon Metals" sneaks in for a
                # report whose central pair trade is long Talen Energy).
                from api.data import yfinance as _yf
                ticker_resolutions: dict[str, str] = {}
                for c in calls_mod.calls_for_report(report.id):
                    if c.asset and c.asset not in ticker_resolutions:
                        try:
                            name = _yf.get_ticker_name(c.asset)
                        except Exception:  # noqa: BLE001
                            name = None
                        if name:
                            ticker_resolutions[c.asset] = name
                gl_result = glossary.build(
                    edited_prose=edited,
                    ticker_resolutions=ticker_resolutions or None,
                )
                gl_text = gl_result.text or ""
                _record(report.id, wd, gl_result)
                _write(wd / "glossary.md", gl_text)
                if not is_empty(gl_text):
                    # Strip the model's leading `# Glossary` heading; the
                    # template adds its own. Defensive against the model
                    # forgetting the heading or wrapping it differently.
                    body = gl_text
                    for line in gl_text.splitlines():
                        if line.lstrip().startswith("# "):
                            after = gl_text.split(line, 1)[1]
                            body = after.lstrip("\n")
                            break
                    glossary_md = body
            except Exception:  # noqa: BLE001
                log.exception("glossary build failed (non-blocking)")

        cited_sections, ordered_sources = attach_inline_citations(
            raw_sections, _read_sources(wd),
        )
        top_dom, dom_share = domain_distribution(ordered_sources)

        # Position-table rows for the cover page: top-conviction calls extracted
        # for this report, capped to keep the cover from sprawling.
        report_calls = calls_mod.calls_for_report(report.id)[:8]
        positions_table = [
            {
                "asset": c.asset,
                "direction": c.direction.value,
                "horizon_days": c.horizon_days,
                "target": c.target_level,
                "conviction": c.conviction,
            }
            for c in report_calls
        ]

        opts = report.render_options or {}
        # Cover title cap. The theme often comes in as a 20+ word sentence
        # ("The operator complex is mispriced against the uranium tape, and
        # TMI is the SMR obituary written by the buyer"); on a cover that
        # wraps to four lines and buries the lede. Cap to ~6 words / 50
        # chars and let the subtitle carry the detail. Already-short themes
        # pass through untouched.
        title_full = report.theme.title() if report.theme.islower() else report.theme
        title_cover = _cover_title(title_full)
        render_kwargs: dict[str, object] = dict(
            out_path=out,
            title=title_cover,
            subtitle=subtitle or parsed.get("opening", "").split("\n")[0][:120],
            date=date.today().isoformat(),
            contributors=contributors_credits,
            sections=cited_sections,
            house_view_top=parsed.get("house_view_top"),
            house_view_bottom=parsed.get("house_view_bottom"),
            disagreement=parsed.get("disagreement") or None,
            bear_case=parsed.get("bear_case") or None,
            glossary=glossary_md,
            positions=positions_table or None,
            read_minutes=8,
            sources=ordered_sources,
            hide_bylines=bool(opts.get("hide_bylines")),
            hide_positions=bool(opts.get("hide_positions")),
            hide_disclosures=bool(opts.get("hide_disclosures")),
        )
        render_pdf(**render_kwargs)  # type: ignore[arg-type]

        # Stylist re-render pass: inspect the just-rendered PDF for layout
        # issues (large bottom gaps from charts that pushed to the next page)
        # and, if any are found, ask the Stylist to insert page-break markers
        # in the prose. If the prose changes, re-parse + re-render. Best
        # effort: any failure leaves the original PDF intact. Skipped on test
        # mode (the smoke pipeline doesn't deserve the doubled render time).
        if report.mode != ReportMode.test:
            try:
                from api.agents.stylist import Stylist, merge as merge_stylist
                from api.render.layout import format_for_prompt, summarise_layout
                pages = summarise_layout(out)
                layout_report = format_for_prompt(pages)
                if "no notable layout issues" not in layout_report:
                    stylist = Stylist(cost, audit=audit)
                    st = stylist.polish_with_layout(
                        edited_prose=edited, layout_report=layout_report,
                    )
                    _record(report.id, wd, st)
                    polished = merge_stylist(edited, st.text or "")
                    if polished != edited:
                        _write(wd / "styled.md", polished)
                        parsed2 = parse_edited(polished)
                        raw2 = _sections_for_render(parsed2, wd)
                        cited2, _ord2 = attach_inline_citations(raw2, _read_sources(wd))
                        render_kwargs["sections"] = cited2
                        render_kwargs["subtitle"] = (
                            subtitle or parsed2.get("opening", "").split("\n")[0][:120]
                        )
                        render_kwargs["house_view_top"] = parsed2.get("house_view_top")
                        render_kwargs["house_view_bottom"] = parsed2.get("house_view_bottom")
                        render_kwargs["disagreement"] = parsed2.get("disagreement") or None
                        render_kwargs["bear_case"] = parsed2.get("bear_case") or None
                        render_pdf(**render_kwargs)  # type: ignore[arg-type]
                        cited_sections = cited2
                        edited = polished
            except Exception:  # noqa: BLE001
                log.exception("stylist layout pass failed (non-blocking)")
        # Reading-time + claim-density. Computed off the cited section
        # bodies (markdown links count as the claim signal -- one link
        # per claim is the analyst-prompt convention). Persisted on the
        # Report row so the dashboard can render it without re-parsing.
        word_count = 0
        link_count = 0
        for s in cited_sections:
            words = re.findall(r"\b\w+\b", s.body_md)
            word_count += len(words)
            link_count += len(re.findall(r"\[[^\]]+?\]\(https?://", s.body_md))
        # 220 wpm is a reasonable middle ground for non-fiction analytical
        # prose -- light enough for casual readers, fast enough that a
        # 2000-word report doesn't claim to need 12 minutes.
        read_minutes_val = max(1, round(word_count / 220.0)) if word_count else 0
        # Density expressed as links per 100 words. Below ~1 reads as
        # hand-wavy; above ~4 reads as overstuffed footnotes.
        density = (link_count * 100.0 / word_count) if word_count else 0.0

        with Session(engine) as session:
            r = session.get(Report, report.id)
            if r:
                r.pdf_path = str(out)
                r.top_domain = top_dom
                r.max_domain_share = dom_share
                r.word_count = word_count
                r.read_minutes = read_minutes_val
                r.claim_density = round(density, 2)
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

        return _next_stage(stage, report.mode)

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
            # Append a calibration footer to the feedback so the persona's
            # log accumulates a track record over time. Best-effort: a
            # missing calibration block doesn't suppress the feedback itself.
            body = fb.text
            try:
                from api import calibration as cal_mod
                cal = cal_mod.for_persona(c["slug"])
                cal_block = cal_mod.format_for_feedback(cal)
                if cal_block:
                    body = f"{fb.text}\n\n_Calibration:_\n{cal_block}"
            except Exception:  # noqa: BLE001
                log.exception("calibration footer failed for %s", c["slug"])
            try:
                from api.feedback import append_to_persona
                if not append_to_persona(c["slug"], body=body, report_id=report.id):
                    log_entries.append(f"## {c['name']}\n\n_persona not found in DB_\n{body}\n")
                    continue
            except Exception as e:  # noqa: BLE001
                log_entries.append(f"## {c['name']}\n\n_failed to append: {e}_\n{body}\n")
                continue
            log_entries.append(f"## {c['name']}\n\n{body}\n")

        _write(wd / "feedback.md", "\n\n---\n\n".join(log_entries) or "_No feedback written._\n")

        # After feedback lands, refresh the recruiter's recommendation queue.
        # Best-effort -- don't fail the report if the review hits a snag.
        try:
            from api.recruiter_review import refresh_recommendations
            refresh_recommendations()
        except Exception:  # noqa: BLE001
            log.exception("recruiter review failed (non-blocking)")
        return _next_stage(stage, report.mode)

    if stage == ReportStage.housekeeping:
        # Close-the-loop pass: update the rolling house view, tag the report,
        # generate the auto-thread, capture voice stats. Each step is
        # best-effort -- a failure in one shouldn't prevent the others.
        # (Call extraction now runs inline at render so the cover can show
        # positions; we don't extract again here.)
        from api import house_view as house_view_mod
        from api import tagger as tagger_mod
        from api import voice_stats as voice_mod

        # Prefer the audited prose for tagging / call extraction; fall back
        # to plain edited if the audit was skipped.
        prose = _read(wd / "audited.md").strip() or _read(wd / "edited.md")
        prose = _strip_conviction_tags(prose)
        brief = _read(wd / "brief.md")
        contributors = _resolved_contributors(report, brief)

        # 1. House view -- EIC voice, expensive but central.
        if prose.strip():
            try:
                eic = EditorInChief(cost, audit=audit, model=models["editor"])
                hv = eic.update_house_view(
                    prior_view=house_view_mod.get(),
                    theme=report.theme,
                    edited_report=prose,
                )
                house_view_mod.upsert(hv.text, last_report_id=report.id)
                _record(report.id, wd, hv)
                _write(wd / "house-view-after.md", hv.text)
            except Exception:  # noqa: BLE001
                log.exception("house view update failed (non-blocking)")

        # 2. Theme + ticker tagger (Haiku).
        if prose.strip():
            try:
                tagger = tagger_mod.Tagger(cost, audit=audit)
                tag_result = tagger.tag(prose=prose)
                tickers, themes = tagger_mod.parse(tag_result.text)
                tagger_mod.persist(report.id, tickers, themes)
                _record(report.id, wd, tag_result)
            except Exception:  # noqa: BLE001
                log.exception("theme tagging failed (non-blocking)")

        # 3. Performance ledger -- already handled inline at render time so
        # the cover page can show positions; nothing to do here.

        # 4. Auto-thread (Haiku). 5-tweet distillation of opening + bottom
        # line. Persisted to wd/thread.md so the dashboard can show it.
        if prose.strip():
            try:
                from api.agents.threader import Threader
                parsed_for_thread = parse_edited(prose)
                threader = Threader(cost, audit=audit)
                tr = threader.thread(
                    theme=report.theme,
                    opening=str(parsed_for_thread.get("opening") or ""),
                    house_view_bottom=str(parsed_for_thread.get("house_view_bottom") or ""),
                )
                _write(wd / "thread.md", tr.text)
                _record(report.id, wd, tr)
            except Exception:  # noqa: BLE001
                log.exception("auto-thread failed (non-blocking)")

        # 5. Voice stats -- pure Python, per contributor's pre-edit draft.
        for c in contributors:
            try:
                draft = _strip_heading(_read(wd / f"section-{c['slug']}.md"))
                if not draft.strip():
                    continue
                metrics = voice_mod.compute(draft)
                voice_mod.record(report.id, c["slug"], metrics)
            except Exception:  # noqa: BLE001
                log.exception("voice stats failed for %s", c["slug"])

        return _next_stage(stage, report.mode)

    return ReportStage.done


# ---------- failure-marker scrubber ----------

# Patterns that indicate a pipeline failure leaking into prose. These come
# from our own placeholders (research/draft/rebuttal timeouts and exceptions),
# from analyst self-narration ("the upstream data pull failed", "the comps
# retry is in the next cycle"), and from agent-loop halts ("max iterations
# reached"). The audit stage strips them so a reader never sees pipeline
# plumbing in the rendered PDF.
_FAILURE_LINE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Bracketed placeholders we generate ourselves.
    re.compile(r"\[(?:research|draft|rebuttal|call)[^\]]*?(?:failed|timed out|halted)[^\]]*\]", re.I),
    re.compile(r"\[agent[^\]]*?(?:halted|max iterations)[^\]]*\]", re.I),
    re.compile(r"\[red-?team[^\]]*?failed[^\]]*\]", re.I),
    # Italicised "_red-team pass failed: ..._" form from redteam.py.
    re.compile(r"_(?:red-?team|audit|coverage|charts?)[^_\n]*?failed[^_\n]*_", re.I),
    # Editor handwave forms: "[Editor's note: ... data pull failed at draft
    # time. Section pulled.]" style. The audit gate also detects these
    # downstream and drops the whole section block.
    re.compile(r"\[Editor'?s? note:[^\]]*?(?:failed|pulled|unavailable|missing)[^\]]*\]", re.I),
    re.compile(r"\[section\s+(?:pulled|omitted|unavailable)[^\]]*\]", re.I),
)
# Sentence-level admissions that also need stripping -- whole sentence goes.
_FAILURE_SENTENCE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"[^.!?\n]*\b(?:upstream\s+(?:data|tool)\s+(?:pull|call)\s+failed|tool\s+(?:call|output)\s+failed)\b[^.!?\n]*[.!?]?", re.I),
    re.compile(r"[^.!?\n]*\bretry(?:\s+is)?\s+(?:in\s+the\s+)?next\s+cycle\b[^.!?\n]*[.!?]?", re.I),
    re.compile(r"[^.!?\n]*\bmax\s+iterations\s+reached\b[^.!?\n]*[.!?]?", re.I),
    re.compile(r"[^.!?\n]*\bagent\s+halted\b[^.!?\n]*[.!?]?", re.I),
)


# Body content under this many non-marker characters reads as a recovery
# stub, not a section. Picked from inspecting the failure mode the reviewer
# flagged: a real analyst section is 800+ chars; the "Section pulled."
# editor handwave was ~120 chars including the marker itself.
_STUB_BODY_THRESHOLD = 120


def _is_stub_body(body: str) -> bool:
    """True if a section body is essentially a recovery note rather than prose.
    Strips failure markers + the "(section omitted...)" sentinel + whitespace
    and checks the residue length."""
    if not body or not body.strip():
        return True
    cleaned, _ = _scrub_failure_markers(body)
    cleaned = re.sub(r"_\(section omitted[^)]*\)_", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return len(cleaned) < _STUB_BODY_THRESHOLD


def _stub_contributor_slugs(
    wd: Path, contributors: list[dict[str, str]],
) -> list[str]:
    """Slugs whose `section-{slug}.md` file is missing, empty, or a stub.
    Used at edit stage to tell the EIC which beats need folding into a
    neighbour rather than rendered as their own (broken) section."""
    out: list[str] = []
    for c in contributors:
        body = _read(wd / f"section-{c['slug']}.md")
        if _is_stub_body(_strip_heading(body) if body.strip() else ""):
            out.append(c["slug"])
    return out


def _drop_stub_revised_sections(edited: str) -> tuple[str, list[str]]:
    """Hard gate: walk REVISED SECTIONS blocks and remove any whose body is a
    recovery stub. Returns (rewritten, dropped_headings). The render stage
    must NEVER show a section header followed by "[section pulled]" -- that
    leaks pipeline state into the reader's eye."""
    m = _REVISED_RE.search(edited)
    if not m:
        return edited, []
    block = m.group(1)
    dropped: list[str] = []
    kept_chunks: list[str] = []
    last = 0
    for sm in _SECTION_BLOCK_RE.finditer(block):
        body = sm.group("body").strip()
        if _is_stub_body(body):
            dropped.append(sm.group("heading").strip())
            kept_chunks.append(block[last:sm.start()])
            last = sm.end()
            continue
    if not dropped:
        return edited, []
    kept_chunks.append(block[last:])
    new_block = "".join(kept_chunks)
    rewritten = edited[:m.start(1)] + new_block + edited[m.end(1):]
    return rewritten, dropped


def _scrub_failure_markers(text: str) -> tuple[str, list[str]]:
    """Strip pipeline failure markers from prose. Returns (cleaned, found).

    `found` is a list of the matched fragments (truncated) for surfacing on
    the report row so the operator can see what was suppressed."""
    if not text or not text.strip():
        return text, []
    found: list[str] = []
    out = text
    for rx in _FAILURE_LINE_PATTERNS:
        for m in rx.finditer(out):
            snippet = m.group(0).strip()
            if snippet:
                found.append(snippet[:120])
        out = rx.sub("", out)
    for rx in _FAILURE_SENTENCE_PATTERNS:
        for m in rx.finditer(out):
            snippet = m.group(0).strip()
            if snippet:
                found.append(snippet[:120])
        out = rx.sub("", out)
    # Collapse blank lines created by sentence/paragraph removal so the
    # rendered prose doesn't show gaping holes.
    out = re.sub(r"\n[ \t]*\n[ \t]*\n+", "\n\n", out)
    # If a section body collapses to whitespace after scrubbing, tag it so
    # the editor / renderer sees a clear gap rather than an empty paragraph
    # that reads as prose-by-omission.
    out = re.sub(
        r"(\n## [^\n]+\n+)(\s*)(?=\n## |\n# |\Z)",
        lambda m: m.group(1) + "_(section omitted: data unavailable)_\n\n",
        out,
    )
    return out, found


# ---------- differentiation check ----------

# Lexical claim-overlap threshold. Above this, two sections are doing the
# same job: same evidence, same direction, same vocabulary. The editor
# should either compress one or kick them onto distinct epistemological
# axes. 0.40 picked from inspecting cases the user flagged: legitimately
# differentiated sections land 0.15-0.30; the duplicated cases land >0.45.
_OVERLAP_THRESHOLD = 0.40

_STOP_WORDS = frozenset((
    "the a an and or but if then so of to in on at by for with from as is "
    "are was were be been being have has had do does did this that these "
    "those it its their there here we us our you your they them he she his "
    "her not no yes can could should would will may might one two three "
    "into about after before over under up down out off through which what "
    "who whom whose when where why how also more most less least very just "
    "than other another any some all such only each per while because"
).split())

_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z'-]+")


def _content_shingles(text: str) -> set[tuple[str, str, str]]:
    """Word trigrams over content tokens (stopwords stripped, lowered).

    Trigrams over content words are a cheap proxy for shared claims --
    "filings show capex pacing" overlaps with "show capex pacing slowing"
    on the trigram (show, capex, pacing) regardless of surrounding text.
    More robust than bag-of-words (a single shared word doesn't trip
    the threshold) and cheaper than embedding-based similarity."""
    tokens = [
        t.lower() for t in _TOKEN_RE.findall(text)
        if t.lower() not in _STOP_WORDS and len(t) > 2
    ]
    return {(tokens[i], tokens[i + 1], tokens[i + 2]) for i in range(len(tokens) - 2)}


def _compute_section_overlap(
    sections_by_slug: dict[str, dict[str, str]],
) -> list[tuple[str, str, float]]:
    """Pairwise content-trigram overlap (Jaccard). Returns pairs above
    _OVERLAP_THRESHOLD only, sorted by overlap descending."""
    shingles = {slug: _content_shingles(s["body"]) for slug, s in sections_by_slug.items()}
    out: list[tuple[str, str, float]] = []
    slugs = sorted(sections_by_slug.keys())
    for i, a in enumerate(slugs):
        sa = shingles[a]
        if not sa:
            continue
        for b in slugs[i + 1:]:
            sb = shingles[b]
            if not sb:
                continue
            inter = len(sa & sb)
            union = len(sa | sb)
            jaccard = inter / union if union else 0.0
            if jaccard >= _OVERLAP_THRESHOLD:
                out.append((a, b, jaccard))
    out.sort(key=lambda t: t[2], reverse=True)
    return out


# ---------- helpers ----------

_COVER_TITLE_MAX_CHARS = 50
_COVER_TITLE_MAX_WORDS = 6


def _cover_title(title: str) -> str:
    """Trim a long theme sentence to a punchy cover-friendly headline.

    A 26-word theme wraps to four lines on the cover and reads like an
    abstract; the previous reports landed two-word titles plus a subtitle
    and the trade-off the reviewer flagged was that the long form was
    spelling out the conclusion before the reader even opened the PDF.
    Strategy: split on the first colon (most long themes use one to
    separate hook from detail), then take up to N words / chars."""
    t = (title or "").strip()
    if not t:
        return t
    # Prefer the pre-colon hook if there is one and it's already short.
    if ":" in t:
        hook = t.split(":", 1)[0].strip()
        if 2 <= len(hook.split()) <= _COVER_TITLE_MAX_WORDS and len(hook) <= _COVER_TITLE_MAX_CHARS:
            return hook
    if len(t) <= _COVER_TITLE_MAX_CHARS and len(t.split()) <= _COVER_TITLE_MAX_WORDS:
        return t
    words = t.split()
    truncated = " ".join(words[:_COVER_TITLE_MAX_WORDS]).rstrip(",;:.-")
    if len(truncated) > _COVER_TITLE_MAX_CHARS:
        truncated = truncated[:_COVER_TITLE_MAX_CHARS].rstrip(",;:.- ") + "…"
    return truncated


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


_REBUTTAL_BLOCK_RE = re.compile(
    r"^##\s+(?P<author>.+?)\s+(?:—|-+)\s+(?P<role>.+?)\n+(?P<body>.+?)(?=\n## |\Z)",
    re.S | re.M,
)


def _parse_rebuttals(md: str) -> list[dict[str, str]]:
    """Pull `## Author — Role\\n\\nbody` blocks out of rebuttals.md."""
    out: list[dict[str, str]] = []
    for m in _REBUTTAL_BLOCK_RE.finditer(md):
        body = m.group("body").strip()
        if not body:
            continue
        out.append({
            "author": m.group("author").strip(),
            "role": m.group("role").strip(),
            "body": body,
        })
    return out


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

_HOUSE_TOP_RE   = re.compile(r"^# HOUSE VIEW \(TOP\)\s*\n(.+?)(?=\n# |\Z)", re.S | re.M)
_HOUSE_BOT_RE   = re.compile(r"^# HOUSE VIEW \(BOTTOM\)\s*\n(.+?)(?=\n# |\Z)", re.S | re.M)
_OPENING_RE     = re.compile(r"^# OPENING\s*\n(.+?)(?=\n# |\Z)", re.S | re.M)
_CLOSING_RE     = re.compile(r"^# CLOSING\s*\n(.+?)(?=\n# |\Z)", re.S | re.M)
_DISAGREE_RE    = re.compile(r"^# DISAGREEMENT\s*\n(.+?)(?=\n# |\Z)", re.S | re.M)
_BEAR_CASE_RE   = re.compile(r"^# BEAR CASE\s*\n(.+?)(?=\n# |\Z)", re.S | re.M)
# Stop at ANY top-level heading after REVISED SECTIONS so DISAGREEMENT,
# BEAR CASE, HOUSE VIEW (BOTTOM) etc. don't get swallowed into the section
# blob below.
_REVISED_RE     = re.compile(r"^# REVISED SECTIONS\s*\n(.+?)(?=\n# |\Z)", re.S | re.M)
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
    # "(none)" or empty -> the renderer skips the callout block.
    def _maybe(s: str) -> str:
        return "" if not s or s.strip().lower().startswith("(none)") else s

    return {
        "opening": grab(_OPENING_RE),
        "house_view_top": grab(_HOUSE_TOP_RE),
        "sections": sections,
        "disagreement": _maybe(grab(_DISAGREE_RE)),
        "bear_case": _maybe(grab(_BEAR_CASE_RE)),
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
            return f'\n<figure><img src="{path.as_uri()}" alt="{fname}"></figure>\n'
        # Substitute the next unused chart in the directory.
        for cand in available:
            if cand.name not in used:
                used.add(cand.name)
                return f'\n<figure><img src="{cand.as_uri()}" alt="{cand.name}"></figure>\n'
        # No charts left. Drop the reference quietly.
        return ""

    return _CHART_TAG_RE.sub(repl, body)
