"""Reconciler. One holistic pass over the assembled draft that fixes
cross-section consistency, strips process artifacts, and tidies reference
hygiene. Replaces the per-stage validator-and-rerun pattern: a single
strong-model call seeing the full draft is cheaper and produces tighter
prose than re-invoking upstream personas on every contradiction.

The reconciler edits in place. It does not redraft. Its output keeps the
EIC's structured headings (`# OPENING`, `# HOUSE VIEW (TOP)`,
`# REVISED SECTIONS`, etc.) so the downstream parser keeps working.

Two trailer sections carry structured directives back to the pipeline:
- `# RECONCILER POSITION TABLE` -- the cover position table after fixes
  (added rows for tickers with named PTs in the body, dropped rows whose
  direction the body contradicts).
- `# RECONCILER CHANGE LOG` -- one bullet per fix with a one-line rationale.
  The stage handler peels both off, persists the calls update, and writes
  the change log to a sibling file for human review.
"""

from __future__ import annotations

import json

from api.agents.base import Agent, AgentResult
from api.agents.cost import CostTracker
from api.settings import settings


class Reconciler(Agent):
    role = "reconciler"
    # Strong model by default. The whole point of this stage is that one good
    # final pass is cheaper than the multi-agent reruns it replaces -- don't
    # cut corners on the model picking which side of a contradiction wins.
    default_model = settings.model_opus

    def __init__(self, cost: CostTracker, **kwargs: object) -> None:
        super().__init__(
            persona_path=settings.team_dir / "editor-in-chief.md",
            cost=cost,
            **kwargs,  # type: ignore[arg-type]
        )

    def reconcile(
        self,
        *,
        draft: str,
        contributors: list[dict[str, str]],
        position_table: list[dict[str, object]],
        redteam_prose: str | None = None,
    ) -> AgentResult:
        roster_lines = [
            f"- `{c['slug']}` -- {c['name']}, {c['role']}" for c in contributors
        ]
        roster_blob = "\n".join(roster_lines) or "(none)"

        # The position table the renderer is about to put on the cover. The
        # reconciler is the only place this gets aligned to the body before
        # the PDF is rendered, so it sees the table as-is and is asked to
        # output the reconciled version.
        if position_table:
            cover_lines = []
            for c in position_table:
                target = c.get("target_level")
                target_s = f"{target}" if target is not None else "—"
                cover_lines.append(
                    f"- `{c.get('asset')}` | {c.get('direction')} | "
                    f"conviction c{c.get('conviction', 3)} | PT {target_s} | "
                    f"by `{c.get('contributor_slug')}`"
                )
            cover_blob = "\n".join(cover_lines)
        else:
            cover_blob = "(empty -- no positions extracted yet)"

        redteam_blob = (
            f"\n\n# RED-TEAM PROSE (Saoirse Mok, devils-advocate)\n\n{redteam_prose}\n"
            if redteam_prose and redteam_prose.strip() else ""
        )

        prompt = f"""You are the firm's reconciler. One pass over the full assembled draft below. Fix cross-section consistency, strip process artifacts, tidy references. You EDIT the draft in place -- you do not redraft. Voice is preserved per section. Argument structure is preserved.

Per-stage validators that fail and trigger reruns are expensive and tend to make worse output. You see the whole thing at once, you decide.

# WHAT TO FIX

## 1. Cross-section consistency

- **Trade direction vs PT vs current price.** If a section says "long TICKER, PT $X, current $Y" but $X < $Y (or "short TICKER, PT $X" with $X > $Y), the section contradicts itself. Pick whichever side of the contradiction the surrounding argument supports, fix the other. Log the call you made in the change log.
- **Cover position table.** Every ticker with a named PT in any section must appear on the cover. Conversely, the cover cannot list a direction the body doesn't defend. You output the reconciled position table in `# RECONCILER POSITION TABLE` below; the pipeline persists it as the cover.
- **Conviction tags.** If the report's argument is that the LONG leg is the trade, a long should not carry lower conviction than a fade unless the prose explicitly justifies the gap. Bump or trim a `{{c1}}`..`{{c5}}` tag where the body's emphasis demands it. Keep the tag syntax verbatim.
- **Date conflicts for the same event.** If multiple sections cite different dates for what looks like the same milestone, reconcile to one timeline. If the dates actually refer to distinct milestones, explain the distinction once where it first appears and use consistent dates everywhere else.

## 2. Process artifacts

- **Strip working notes.** Any paragraph that is clearly an analyst's working note rather than reader-facing prose (instructions about layout, "leads. follows the inflection paragraph...", TODOs, internal markers) gets removed. Don't soften them, don't keep a placeholder -- delete.
- **Masthead vs sections.** Every persona named in the masthead/cover must have either a named attributed section in `# REVISED SECTIONS` or be removed. If a section has no clear author but its content fits a masthead persona's role, attribute it to that persona. If a masthead persona has nothing to attribute, strip them in the change log (the renderer will follow the section list).
- **Strip internal HR metadata** from masthead role descriptions: drop "(Temp, One-Report Engagement)", "(probationary)", "(contractor)", and similar. Roles read clean.

## 3. Reference hygiene

- **First mention of a person or company.** Use the full name + title on first reference (e.g. "Jensen Huang, NVIDIA's CEO"), surname-only or ticker-only on subsequent. Companies introduced by surname-style shorthand (e.g. "Mok said") only if the persona's full name has appeared upstream.
- **Glossary.** Dedupe entries; fix obvious typos. Don't expand it.
- **Redundant section openings.** When two sections restate the same setup paragraph in different voices, keep the strongest version, cut the others. The reader never sees the same argument twice.

# CONSTRAINTS

- Output the FULL draft. Preserve every literal `# OPENING` / `# HOUSE VIEW (TOP)` / `# REVISED SECTIONS` / `## <heading>` / `**author:**` / `**role:**` / `# DISAGREEMENT` / `# BEAR CASE` / `# HOUSE VIEW (BOTTOM)` / `# CLOSING` heading you find.
- Markdown links `[anchor](url)`, `[chart: filename.png]` tags, and conviction tags `{{c1}}`..`{{c5}}` must round-trip verbatim except where you intentionally change a conviction tag.
- If you cannot fix something safely (the right call is genuinely ambiguous), leave the prose alone and add a `[reconciler-flag: <one-line description>]` inline at the spot for human review. Don't fabricate to paper over.
- Do NOT introduce new numerical claims. The numerical audit ran upstream; your edits stay neutral on figures unless you're choosing one of two existing figures over the other.

# CONTRIBUTORS ON THIS REPORT

{roster_blob}

# CURRENT COVER POSITION TABLE (from upstream extraction)

{cover_blob}

# DRAFT

{draft}{redteam_blob}

# OUTPUT FORMAT

Reconciled draft, then exactly two trailer sections:

```
<full reconciled draft -- same headings as input>

# RECONCILER POSITION TABLE
<JSON array of objects -- one per cover row AFTER your fixes. Use exactly this shape:>
```json
[
  {{
    "asset": "TICKER",
    "direction": "long|short|fade|avoid",
    "conviction": 1-5,
    "target_level": <number or null>,
    "contributor_slug": "<slug from the contributor list above, or `devils-advocate`>",
    "claim_text": "<short prose anchor for the call, ~12 words>"
  }}
]
```
<If the reconciled cover should be empty, return an empty array `[]`.>

# RECONCILER CHANGE LOG
- <one bullet per fix you made. Format: `<section> -- <what changed> -- <why (one line)>`. If you made zero changes, write `(none -- draft was already clean)` and stop.>
```

Stop after the change log. No closing prose."""
        # Big draft, big context, big output budget. Opus on the full draft is
        # the deliberate cost trade against multiple cheaper rerun loops.
        return self.run(prompt, max_tokens=12288, max_iters=1)


_POSITION_TABLE_HEADING = "# RECONCILER POSITION TABLE"
_CHANGE_LOG_HEADING = "# RECONCILER CHANGE LOG"


def split_output(text: str) -> tuple[str, list[dict[str, object]], str]:
    """Peel the trailer sections off the reconciler's output.

    Returns (reconciled_draft, position_table_rows, change_log_md). The
    position table is parsed from the JSON array inside its trailer; on any
    parse failure it returns an empty list and the original draft is kept
    on the cover (the position_audit upstream still defended each row).
    """
    pos_idx = text.find(_POSITION_TABLE_HEADING)
    log_idx = text.find(_CHANGE_LOG_HEADING)

    if pos_idx == -1 and log_idx == -1:
        return text.strip(), [], ""

    head_idx = pos_idx if pos_idx != -1 else log_idx
    draft = text[:head_idx].rstrip()

    position_rows: list[dict[str, object]] = []
    if pos_idx != -1:
        end = log_idx if log_idx > pos_idx else len(text)
        block = text[pos_idx + len(_POSITION_TABLE_HEADING):end]
        position_rows = _parse_position_block(block)

    change_log = ""
    if log_idx != -1:
        change_log = text[log_idx + len(_CHANGE_LOG_HEADING):].strip()

    return draft, position_rows, change_log


def _parse_position_block(block: str) -> list[dict[str, object]]:
    """Pull the JSON array out of a fenced or bare position-table block."""
    s = block.strip()
    # Try fenced ```json ... ``` first; fall back to first '[' .. last ']'.
    fence_start = s.find("```")
    if fence_start != -1:
        # Skip optional language tag
        nl = s.find("\n", fence_start)
        if nl != -1:
            inner_start = nl + 1
            fence_end = s.find("```", inner_start)
            if fence_end != -1:
                s = s[inner_start:fence_end].strip()
    lb, rb = s.find("["), s.rfind("]")
    if lb == -1 or rb == -1 or rb <= lb:
        return []
    try:
        arr = json.loads(s[lb:rb + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(arr, list):
        return []
    out: list[dict[str, object]] = []
    valid_dirs = {"long", "short", "fade", "avoid"}
    for row in arr:
        if not isinstance(row, dict):
            continue
        asset = str(row.get("asset", "")).strip().upper().replace(" ", "")
        direction = str(row.get("direction", "")).strip().lower()
        slug = str(row.get("contributor_slug", "")).strip()
        if not asset or direction not in valid_dirs or not slug:
            continue
        try:
            conviction = int(row.get("conviction") or 3)
        except (TypeError, ValueError):
            conviction = 3
        target = row.get("target_level")
        try:
            target_f = float(target) if target is not None else None
        except (TypeError, ValueError):
            target_f = None
        out.append({
            "asset": asset,
            "direction": direction,
            "horizon_days": int(row.get("horizon_days") or 90),
            "conviction": max(1, min(5, conviction)),
            "target_level": target_f,
            "contributor_slug": slug,
            "claim_text": str(row.get("claim_text", ""))[:500],
        })
    return out
