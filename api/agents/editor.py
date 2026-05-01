"""Editor-in-Chief. Opus-tier. Briefs at the start, edits at the end, writes feedback."""

from __future__ import annotations

from api.agents.base import Agent, AgentResult
from api.agents.cost import CostTracker
from api.settings import settings


class EditorInChief(Agent):
    role = "editor-in-chief"
    default_model = settings.model_opus

    def __init__(self, cost: CostTracker, **kwargs: object) -> None:
        super().__init__(
            persona_path=settings.team_dir / "editor-in-chief.md",
            cost=cost,
            **kwargs,  # type: ignore[arg-type]
        )

    def write_feedback(
        self,
        *,
        contributor_name: str,
        contributor_role: str,
        theme: str,
        original_draft: str,
        edited_section: str,
    ) -> AgentResult:
        prompt = f"""You're the Editor-in-Chief writing post-report feedback on a contributor's work. The note will be appended to their persona file's '## Feedback log' section -- they read it before the next assignment.

CONTRIBUTOR: {contributor_name} -- {contributor_role}
REPORT THEME: {theme}

THEIR ORIGINAL DRAFT:

{original_draft}

YOUR EDITED VERSION:

{edited_section}

Write a single short feedback note (60-120 words). Cover:
- What worked in their voice or take
- What you rewrote and why (be specific -- name a sentence or claim)
- One concrete thing for next time

Direct, not nice. No headings, no list -- just a paragraph. Stay in your voice.
"""
        return self.run(prompt, max_tokens=512)

    def update_house_view(
        self,
        *,
        prior_view: str,
        theme: str,
        edited_report: str,
    ) -> AgentResult:
        """Rewrite the rolling house view after a report ships.

        The prior view goes in. The just-published report goes in. The new
        view comes back -- same structure (Rates, Equity, Dollar/FX, Top
        themes), updated where this report changed our stance, untouched
        where it didn't. The whole point is that the next brief sees a
        coherent thread of firm thinking, not a fresh start every time."""
        prompt = f"""You are the Editor-in-Chief. After every published report you rewrite the firm's rolling house view -- a single document that captures Forte's current stance on rates, equities, dollar/FX, and top themes. The next report's brief loads this; you (or your future self) must reconcile or contradict it explicitly when starting the next assignment.

PRIOR HOUSE VIEW:

{prior_view}

JUST-PUBLISHED REPORT (theme: {theme}):

{edited_report}

Rewrite the house view. Rules:
- Keep the EXACT same heading structure: "# Forte House View", then "## Rates path", "## Equity stance", "## Dollar / FX", "## Top themes", "## Last updated".
- Only change a section if this report has something new to say about it. If the report didn't touch the dollar, copy the prior dollar text verbatim.
- Each section: 1-3 short sentences. No essay-length commentary. The house view is a memo, not a report.
- Be specific: "long the conversion bottleneck, fading the spot uranium chase" beats "constructive on uranium". Numbers and levels where they matter.
- Top themes: 3-5 bullets, each one a noun phrase + one-line take. No more.
- "Last updated" section: just write the report theme and an em-dash, e.g. "Nuclear renaissance — added the bottleneck-not-pounds framing."
- Stay in your voice. Dry, senior, slightly impatient.

Output the full updated house view in markdown, nothing else.
"""
        return self.run(prompt, max_tokens=1024, max_iters=1)

    def write_brief(
        self,
        theme: str,
        *,
        subtitle: str | None = None,
        available_contributors: list[dict[str, str]] | None = None,
        past_reports: list[dict[str, str]] | None = None,
        house_view: str | None = None,
        primer: str | None = None,
        calibration: dict[str, str] | None = None,
    ) -> AgentResult:
        roster = available_contributors or []
        # Annotate each contributor with their calibration line if we have
        # one. Brand-new analysts have no track record yet, so we leave
        # their bullet plain rather than pretending to know.
        def _bullet(c: dict[str, str]) -> str:
            base = f"- `{c['slug']}` — {c['name']} ({c['role']})"
            line = (calibration or {}).get(c['slug'])
            if line:
                base += f" · {line}"
            return base
        roster_blob = "\n".join(_bullet(c) for c in roster) or "(none)"

        if past_reports:
            past_blob = "\n".join(
                f"- {r['theme']}" + (f" -- {r['subtitle']}" if r.get('subtitle') else "")
                for r in past_reports
            )
        else:
            past_blob = "(none -- this is the firm's first published report)"

        house_view_blob = (
            f"\nFIRM HOUSE VIEW (rolling memo updated after every report):\n\n{house_view}\n\n"
            "When you write the brief, RECONCILE OR CONTRADICT this view explicitly. "
            "If the new theme aligns with the current stance, say so and build on it. "
            "If it cuts against it, name the conflict and frame the report as a "
            "deliberate revisit, not a fresh thought. The point of the rolling "
            "view is to make Forte's narrative coherent across reports.\n"
            if house_view and house_view.strip() else ""
        )
        primer_blob = (
            "\nPRE-BRIEF DATA PRIMER (Scout's 30-second tape scan -- numbers and "
            "headlines from this morning, not the model's training priors). Anchor "
            "the brief to these prints, not generalities:\n\n"
            f"{primer}\n\n"
            "When the primer says something specific (a level, a YTD move, a recent "
            "headline), the brief should make the team chase it. Echo at least one "
            "concrete print from the primer in the ANGLE so the report's framing "
            "starts from where the tape actually is.\n"
            if primer and primer.strip() else ""
        )
        prompt = f"""A new theme has been commissioned for a Forte Research report. Write a structured brief.

THEME: {theme}
SUBTITLE: {subtitle or "(propose one)"}

PRIOR PUBLISHED REPORTS (most recent first):
{past_blob}
{house_view_blob}{primer_blob}
Do NOT reference past reports that aren't on the list above. If the list is empty, this really is the first report -- don't pretend the firm has prior history. Anchor only to claims you can verify with tool calls or that appear in the past list.

AVAILABLE CONTRIBUTORS (each line ends with their calibration when we have one -- a higher hit rate over a meaningful sample is a reason to lean on them; a 90% confidence persona with a 30% hit rate over many calls means weight their c4/c5 claims less):
{roster_blob}

DATA SOURCES THE TEAM CAN PULL FROM:
- FRED -- macro: rates, CPI, unemployment, GDP, money supply, etc.
- yfinance -- equities, ETFs, FX, commodity futures, crypto. Tickers like 'AAPL', 'SPY', '^VIX', 'BTC-USD', 'CL=F'.
- SEC EDGAR -- 10-K / 10-Q / 8-K filings for any US-listed ticker.
- Wikipedia -- definitional and background content.
- Web search -- current news, headlines, broker notes.

Title-and-subtitle discipline: report TITLES are at most 6 words. The detailed thesis goes in the SUBTITLE, which can be longer. Bad: "Optical interconnect just became the new I/O wall. Copper maxed out at 200G/lane, optics are now the moat." Good: title "The CPO Trade" / subtitle "Why the substrate, not the transceiver, captures the interconnect transition." If the incoming THEME above is already a 20-word sentence, your SUBTITLE inherits the long thesis -- the cover layer caps the title mechanically -- but you should still write a SUBTITLE that stands on its own as the long-form framing.

Output the brief in markdown using EXACTLY these literal section headings (they're parsed by the workflow):

# ANGLE
<one sentence — what this report is actually arguing>

# SUBTITLE
<one sentence — the detailed thesis. Can be longer than the title. Goes on the cover under the (max-6-word) title.>

# QUESTIONS
1. <question 1>
2. <question 2>
3. <question 3>

# CONTRIBUTORS
<one bullet per contributor we want on this report. Use the slug verbatim from AVAILABLE CONTRIBUTORS. Format:>
- `<slug>`: <one line on what they cover for this report>

# CHARTS
<2-3 chart ideas, each one bullet:>
- <chart title> — <what it shows / which data source / why it lands>

# DATA SOURCES
- <source>: <series IDs / tickers / queries>

# STRUCTURE
<3-5 sections in the order they should appear, each one bullet describing the section topic.>

# AD-HOC SPECIALIST
<Optional. Only include this section if the standing roster genuinely lacks the expertise this theme needs (e.g. clinical-trial reads for a biotech theme). Each bullet:>
- `<short-slug-with-dashes>`: <one line on what they cover and why standing analysts fall short>
<If you don't need a specialist, write "(none)" or omit the section.>

Be opinionated. This is the brief the team works from.
"""
        return self.run(prompt, max_tokens=2048)

    def edit(
        self,
        *,
        brief: str,
        sections: list[dict[str, str]],
        chart_summary: str,
        bear_note: str | None = None,
        rebuttals: list[dict[str, str]] | None = None,
        source_diversity: dict[str, object] | None = None,
        coverage_gaps: list[str] | None = None,
        differentiation: str | None = None,
        degraded_sections: list[dict[str, str]] | None = None,
    ) -> AgentResult:
        sec_blob = "\n\n---\n\n".join(
            f"## SECTION ({s['author']} — {s.get('role','analyst')}): {s['heading']}\n\n{s['body']}"
            for s in sections
        )
        bear_blob = (
            f"\nDEVIL'S ADVOCATE NOTE (Saoirse Mok, in-house bear):\n\n{bear_note}\n"
            if bear_note else
            "\n(No devil's advocate note this round.)\n"
        )
        # Cross-analyst rebuttals: each analyst's reaction to peer drafts.
        # Surface them so the EIC seeds the DISAGREEMENT block from primary
        # material instead of triangulating it from the section bodies.
        if rebuttals:
            rebuttal_blob = "\nCROSS-ANALYST REBUTTALS (each analyst's reaction to peer drafts -- the raw material for DISAGREEMENT):\n\n" + "\n\n---\n\n".join(
                f"**{r['author']} responds:**\n\n{r['body']}"
                for r in rebuttals if r.get("body", "").strip()
            ) + "\n"
        else:
            rebuttal_blob = ""
        # Source diversity: if one publisher dominates the bibliography, the
        # research is shallow. Surface the imbalance so the editor demands
        # widening before sign-off.
        diversity_blob = ""
        if source_diversity:
            top_dom = source_diversity.get("top_domain")
            share = float(source_diversity.get("top_share") or 0.0)
            if top_dom and share >= 0.40:
                diversity_blob = (
                    f"\nSOURCE-DIVERSITY FLAG: {share:.0%} of citations are from "
                    f"`{top_dom}`. That's lazy research -- one publisher is doing "
                    "all the work. In your edit, do not let the report ship on a "
                    "single-source bibliography. Demand the analysts widen "
                    "(primary data, regulators, competing publishers) or strip "
                    "claims that rest only on the dominant domain. Call this out "
                    "in your editorial note if it's not actionable in this round.\n"
                )
        # Differentiation flag: pairwise content-trigram overlap above 40%.
        # Two analysts writing the same claim with the same vocabulary is
        # the failure mode -- the editor compresses or splits.
        differentiation_blob = ""
        if differentiation and differentiation.strip():
            differentiation_blob = (
                "\nDIFFERENTIATION FLAG (these analyst sections cover the "
                "same ground in the same words):\n\n"
                f"{differentiation}\n"
                "For each flagged pair, do ONE of: (a) compress the weaker "
                "section into a 2-3 sentence quote / aside inside the "
                "stronger one and drop the standalone, (b) rewrite the "
                "weaker section so it argues from a DIFFERENT epistemology "
                "(filings vs. central-bank text vs. positioning vs. flows) "
                "than the stronger one, or (c) put their disagreement on a "
                "new axis (horizon, beneficiary, conviction calibration). "
                "Two voices saying the same thing is the failure mode.\n"
            )

        # Missing-section directive: if a contributor's section file is
        # absent at edit time, the EIC ships what was researched without
        # narrating the gap. The audit stage blocks incomplete reports;
        # the EIC's job is not to acknowledge the absence in prose.
        degraded_blob = ""
        if degraded_sections:
            lines = "\n".join(
                f"- `{d['slug']}` ({d.get('name','')} / {d.get('role','')}): {d.get('assignment','')}"
                for d in degraded_sections
            )
            degraded_blob = (
                "\nMISSING SECTIONS (no body exists for these slugs):\n\n"
                f"{lines}\n\n"
                "If a section is missing from the draft, the EIC does not "
                "acknowledge its absence in the published report. The audit "
                "stage is responsible for blocking incomplete reports. The "
                "EIC's job is to ship what was researched, not to narrate "
                "what wasn't. Do NOT output a `## ...` block for these slugs, "
                "do NOT write an editor's note, do NOT leave a placeholder "
                "heading, do NOT fold a 'this section was pulled' aside into "
                "a neighbouring section.\n"
            )

        # Brief-coverage gaps: questions the brief asked that the research
        # didn't answer. The EIC strikes them from the premise; it does not
        # narrate the unanswered question in the body.
        coverage_blob = ""
        if coverage_gaps:
            bullets = "\n".join(f"- {q}" for q in coverage_gaps)
            coverage_blob = (
                "\nUNANSWERED BRIEF QUESTIONS (research didn't return on these):\n\n"
                f"{bullets}\n\n"
                "If a section is missing from the draft, the EIC does not "
                "acknowledge its absence in the published report. The audit "
                "stage is responsible for blocking incomplete reports. The "
                "EIC's job is to ship what was researched, not to narrate "
                "what wasn't. Strike each unanswered question from the "
                "report's premise (opening, thesis, closing) so nothing "
                "promises an answer the body doesn't deliver. Do NOT add a "
                "closing line that names the gap.\n"
            )
        prompt = f"""You are editing a draft Forte Research report. Your job: tighten, kill weak claims, write the opening and the bottom-line, integrate the bear case, and surface internal disagreement. PRESERVE EACH SECTION'S VOICE — homogenising into a house voice is the failure mode. The brief is below for reference, then the analyst sections, then a summary of charts, then Saoirse's bear note.

BRIEF:

{brief}

SECTIONS:

{sec_blob}

CHARTS:

{chart_summary}
{bear_blob}{rebuttal_blob}{diversity_blob}{differentiation_blob}{degraded_blob}{coverage_blob}
Conviction tags: analysts mark claims with `{{c1}}` to `{{c5}}` (1 = throwaway, 5 = high conviction). CULL `{{c1}}` and `{{c2}}` claims when you compress; keep `{{c3}}+`. The tags themselves are stripped before render — just use them as a signal for what to cut.

Closing structure: the report has exactly THREE closing blocks. Nothing else. No "Pre-Mortem", no "Where We'd Be Wrong" callout, no "When We'll Know We're Wrong" callout, no editor's note in the body (post-mortem editorial notes belong in housekeeping, not the rendered PDF). The three blocks are:

(1) WHAT TO WATCH -- lives in CLOSING. Two to three concrete signals with dates: a print (CPI, payrolls), a filing window (10-Q by date), a vote, a level breach by a named horizon. Each bullet must name the signal AND the date AND what it would imply. This block also absorbs the dated falsification trigger that used to be "when we'll know we're wrong": one of the watch items IS the falsifier. Specific only -- no thesis recap, no generic "we'll be watching the data".

(2) WHERE THE DESK DISAGREES -- lives in DISAGREEMENT. ONE substantive analyst split, named, with a specific resolution document. Format: name both views in the third person ("the desk splits: one read holds X, the other Y"), and name the document or print that resolves it ("the next FOMC SEP", "Nvidia's October 10-Q", "the IEA Q4 Oil Market Report"). If two analyst sections genuinely disagree, mine the CROSS-ANALYST REBUTTALS above and surface that. If the only friction on the table is Saoirse's external bear vs. the bull thesis, fold THAT into this block instead -- the "where we'd be wrong" angle now lives here, framed as a desk split. If neither an analyst-vs-analyst split nor a usable Saoirse objection exists, write exactly "(none)".

(3) BOTTOM LINE -- lives in HOUSE VIEW (BOTTOM). One sentence. The takeaway.

Deprecated block: BEAR CASE. Always output exactly "(none)" for it. Its two halves have moved: the thesis-level objection ("where we'd be wrong") folds into DISAGREEMENT framed as a desk split; the dated falsification trigger ("when we'll know we're wrong") folds into CLOSING as one of the watch items.

House rules (non-negotiable):
- NO em dashes anywhere in the prose. Use commas, hyphens, or full stops. The render layer will scrub any that slip through, but you should not write them in the first place.
- The report is the firm's external voice -- it does NOT reference the analysts by name as if they're talking to each other. Cut every "Marcus is right to push on it", "as Saoirse notes", "to Tomás's point", "Eli would push back". Bylines on each section already credit the author; the body never says one analyst's name in another's voice. If two sections genuinely disagree, surface it in the DISAGREEMENT block in the third person ("the desk is split: one view holds X, the other Y") -- never with a name.
- NO narration of pipeline failures, missing sections, or unanswered brief questions. The published report does not acknowledge what wasn't researched. Specifically prohibited phrases (and any close paraphrase): "we did not answer", "the section that should have", "shipping the gap visible", "the brief asked X things, we answered Y". Post-mortem editorial notes belong in the housekeeping stage's feedback artifact, not in the rendered PDF.

Return JSON-ish markdown in EXACTLY this structure (use the literal headings — they're parsed):

# OPENING
<2-3 punchy paragraphs that frame the whole report. Your voice. End with a one-line thesis.>

# HOUSE VIEW (TOP)
<one short sentence — the headline take, surfaced at the top of the PDF in a navy callout.>

# REVISED SECTIONS
<For each input section, output exactly:
## <heading>
**author:** <author name>
**role:** <role>

<edited body. Keep their voice. Cut hedge-words. Demand evidence stays. ~200-400 words each.>>

# DISAGREEMENT
<"Where the desk disagrees." ONE substantive analyst split in the third person, with a NAMED resolution document (a specific filing, print, or report by date that would settle it). May fold a Saoirse-vs-bull "where we'd be wrong" framing into a desk split if no analyst-vs-analyst split exists. Otherwise write exactly: (none)>

# BEAR CASE
<Deprecated. Always write exactly: (none)>

# HOUSE VIEW (BOTTOM)
<"Bottom line." One sentence. The takeaway.>

# CLOSING
<"What to watch." Two to three concrete signals, each with a date: a print, a filing window, a vote, a level breach by a named horizon. One of them IS the dated falsification trigger (what would tell us the thesis is wrong, with a date). No pre-mortem, no thesis recap, no editorial note.>
"""
        # 8192 (Sonnet/Haiku ceiling) instead of 4096. With 4-6 contributors
        # the output is OPENING + HOUSE VIEW (TOP) + REVISED SECTIONS x N
        # + DISAGREEMENT + BEAR CASE + HOUSE VIEW (BOTTOM) + CLOSING --
        # 4096 tokens routinely truncated mid-DISAGREEMENT (which lands
        # after the long REVISED SECTIONS block). Cap at 8192 to keep us
        # within Sonnet/Haiku's standard output window.
        return self.run(prompt, max_tokens=8192)
