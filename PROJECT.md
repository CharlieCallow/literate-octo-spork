# Forte Research — Multi-Agent Thematic Research Reports

This is the project spec. Treat it as the source of truth for what we're building, why, and how. Read it at the start of every session before writing or planning anything. If something here is ambiguous, ask before building it.

---

## 1. The vision

Build a web app where I can either type in a research theme or pick from agent-proposed themes, hit go, and get back a polished, chart-heavy, opinionated PDF research report — the kind of thing Citrini, Goldman's Privorotsky note, or a Citi flagship would publish, but produced end-to-end by a small team of AI agents that act like a real research firm.

The agents have personas — names, voices, areas of expertise, opinions. They argue, draft, edit each other, and the report has visible voice — different sections feel like they came from different analysts. The output is serious (real data, real charts, defensible analysis) but enjoyable (Citrini-style punch, dry humour, occasional irreverence).

Reports are accessed via a web dashboard from anywhere. Past reports are browsable. The system gets smarter over time as the team evolves.

---

## 2. The firm (agent architecture)

The whole thing is structured as a small research firm. Each agent has a persona file (`team/<slug>.md`) with: name, role, system prompt, voice notes, areas of expertise, hire date, performance notes. The persona file IS the agent's identity and is loaded into the system prompt at runtime.

### Default starting roster (ship these on first run)

Generate fictional but credible personas — names, brief bios, distinct voices. Lean into it; this is half the fun. Each persona must have a clearly differentiated voice in writing.

1. **Editor-in-Chief.** Runs the show. Reviews drafts, kills weak arguments, demands data, sets the report's structure and angle. Writes the opening and the bottom-line. Also gives feedback to all other personas (see persona evolution below). Voice: senior, dry, slightly impatient, the one who's seen every cycle.
2. **Macro Strategist.** Geopolitics, rates, central banks, commodities, cross-asset. Voice: macro tourist energy, big-picture, willing to make calls.
3. **Equity / Sector Analyst.** Bottom-up, picks names, knows the supply chain. Voice: detail-obsessed, slightly cynical about consensus.
4. **Data & Charts.** The quant. Pulls data, runs the numbers, builds the visualisations. Writes captions and chart commentary. Voice: terse, lets the data talk, occasional dry one-liners.
5. **Scout.** Scans news, filings, sentiment, market moves. Surfaces the daily 10 themes. Voice: rapid-fire, headline-driven, FOMO-aware.
6. **Recruiter.** HR. Proposes new hires when a theme needs expertise the team lacks. Suggests firings (see below). Maintains the team directory. Voice: corporate but self-aware, slight HR-speak parody.

### The Recruiter mechanic

The Recruiter is the only agent that can modify the team directory. Two modes:

- **Ad-hoc.** During a report, if the assembled team flags missing expertise (e.g. the report is on biotech but no one knows clinical trial data), the Recruiter spins up a temporary specialist for that report only. Temporary agents live in `team/temp/` and are auto-deleted after the report is published unless promoted.
- **Permanent.** After a report, if a temporary agent did good work, the Recruiter can propose promoting them. Promotion requires my approval via the dashboard. Promoted agents move to `team/` and are available on future reports.

### Firing

The Recruiter surfaces firing recommendations in the dashboard. Three triggers:

- **Underperformance.** The Editor-in-Chief flags an agent whose sections keep getting heavily rewritten. Tracked as a rewrite-ratio metric.
- **Redundancy.** Two agents covering the same beat. Recruiter recommends keeping the one with better performance metrics.
- **Stale.** No assignments in 30+ days.

Firing recommendations always come with reasoning. I approve or reject from the dashboard. Fired agents are archived (not deleted) so I can rehire them.

### Persona evolution / feedback

After every report, the Editor-in-Chief writes a short feedback note on each contributing agent — what worked, what didn't, voice notes, suggestions. These notes accumulate in each agent's persona file under a `## Feedback log` section. On the next report, when the agent is loaded, recent feedback is included in their system prompt as part of their identity. This is how voices sharpen over time.

I should also be able to manually edit any persona file from the dashboard (markdown editor) and changes take effect immediately.

---

## 3. Workflow — how a report gets made

There are two entry paths:

**Path A: Manual theme.** I type a theme in the dashboard ("nuclear renaissance," "second-order effects of GLP-1," etc.) and hit go.

**Path B: Scout-proposed.** Each morning at a configurable time, the Scout runs and produces a digest of the top 10 themes — what's moving, what's underpriced, what's interesting. Delivered to both the dashboard inbox and email. I pick one (or none) to commission.

Once a theme is locked in, the workflow runs roughly as follows. Build it as an explicit state machine, not a prompt chain — each stage writes its output to the report's working directory and updates the report's state in the DB.

1. **Brief.** Editor-in-Chief reads the theme, writes a one-page brief: the angle, the key questions, the structure, who's writing what. This is the only stage where the EIC is in the loop alone — everything else is collaborative.
2. **Recruit (if needed).** Recruiter reviews the brief. If the team is missing expertise, spins up a temporary specialist with a tailored system prompt. Logged so I can see who got hired for what.
3. **Research.** Each contributing analyst runs their research independently — web search, data fetches, primary sources. Outputs structured notes (claims + evidence + sources) into the working directory. They can call data tools (FRED, yfinance, etc.) directly.
4. **Charts.** Data & Charts agent reviews the research notes, decides what visualisations would land hardest, pulls the data, generates charts using the house style, writes captions. Charts live as PNG files in `reports/<id>/charts/`.
5. **Draft.** Each analyst drafts their section in markdown, with chart references inline. Voices differ — preserve that. Sections are tagged with the author so the editor knows whose voice to preserve in revision.
6. **Edit.** Editor-in-Chief reads everything, restructures if needed, kills weak claims, tightens, writes the opening and closing. Crucially: preserves each section's voice — the editor's job is coherence, not homogenisation.
7. **Render.** Markdown → HTML (Jinja template) → PDF (WeasyPrint). PDF lands in storage with a stable URL.
8. **Feedback.** EIC writes feedback notes for each contributing agent. Notes append to persona files. Recruiter reviews team performance metrics, may queue hire/fire recommendations.

Every stage emits events to the dashboard so I can watch progress live. Total time per report: target 5-15 minutes depending on scope.

---

## 4. Tech stack

Keep it as simple as possible. No exotic infra.

- **Backend:** Python 3.11+, FastAPI. Handles agents, data fetching, charts, PDF generation all natively.
- **Frontend:** Next.js (App Router) on Vercel. Free tier fine.
- **Backend hosting:** Railway. One-click Python deploy, has persistent volumes, has scheduled jobs for the daily Scout digest, ~$5/month at our scale.
- **Database:** Postgres on Railway (same project). Stores: reports, agents, personas, theme history, performance metrics, audit log, scheduled jobs.
- **Queue:** No Redis. Just a Postgres table `jobs` and a Python worker process polling it every few seconds. Reports take minutes, this is fine.
- **File storage:** Cloudflare R2 for PDFs and charts (free tier generous). Use a signed URL pattern.
- **Auth:** Single password, stored as a hash in env. HTTP basic auth or a simple session cookie. This is just for me.
- **Email:** Resend or Postmark — whichever is cheapest/easiest — for the daily Scout digest.
- **LLM:** Anthropic API directly. Use the official Python SDK. Model tiering (see §9).

---

## 5. Data sources

Free only. Build adapters for each, expose as Python functions that agents can call as tools.

- **FRED** (`fredapi`). Macro: rates, CPI, unemployment, GDP, money supply, anything the St. Louis Fed has. Free API key. Heaviest usage.
- **yfinance.** Equities, ETFs, FX, commodity futures, crypto. Use it for price/return charts and basic fundamentals.
- **SEC EDGAR.** Filings — 10-K, 10-Q, 8-K. Use the JSON API. Good for deep equity work.
- **Web search + web fetch.** For news, sentiment, primary sources, broker note summaries (where legal). Use Anthropic's web search tool inside agents.
- **Reddit + Hacker News.** Free APIs. For sentiment and "what are people actually talking about." Useful for the Scout.
- **OpenBB Platform.** Aggregator that wraps a lot of free sources (FMP, Polygon free tier, etc.). Worth installing — gives a uniform interface.
- **Wikipedia API.** For background and definitional content. Cheap and surprisingly useful.

Build a `data/` package with one module per source. Each module exposes functions like `get_series(series_id, start, end)` returning a pandas DataFrame in a consistent shape. Agents call these as tools.

Cache aggressively — data fetches go to a Postgres `data_cache` table keyed on `(source, query_hash, fetched_at)`. Default TTL: 1 hour for prices, 1 day for macro, 1 week for filings.

---

## 6. Design system

Forte palette extracted from logo + spec. Hillgate structure preserved, retuned to Forte. Full details in `api/render/styles/forte-palette.json`, `api/render/styles/report.css`, `api/render/styles/forte.mplstyle`.

Charts: matplotlib with house style (navy primary, teal secondary, no chart junk, FT/Bloomberg-clean). PDF: WeasyPrint, A4, 0.75" margins, header bar + page footer. Cover page with logo, title, contributing analysts, read-time estimate. Three callout styles (green/navy/amber) plus a "house view" navy-fill callout for editor takeaways.

---

## 7. Voice & tone

Lean Citrini. Each persona has a distinct point on the spectrum (EIC punchiest, Data&Charts driest, Scout fastest). Opinions mandatory; data backs everything; no McKinsey-speak; no emojis in body; em-dashes used sparingly. Persona files include 2-3 voice samples as few-shot anchors. The EIC's job is coherence, not homogenisation — flattening voice is the failure mode.

---

## 8. Dashboard

Home / new report / report viewer (live progress + embedded PDF) / archive / team / settings / audit log. Forte palette throughout.

---

## 9. Cost guardrails

Hard caps per report ($1) and per day ($5), warnings at 70%/60%. Model tiering: Haiku for Scout + grunt work; Sonnet for analysts/drafting (workhorse); Opus for EIC final pass only. Track per-call cost, halt on cap hit.

---

## 10. Build order — milestones

- **M1** — Skeleton + one report end-to-end: 3-agent pipeline (EIC + Analyst + D&C), Hillgate-derived PDF, one FRED chart, one sample report, Next.js home with embedded PDF.
- **M2** — Full 6-persona team, complete state machine, theme intake, multi-source data, cost caps enforced.
- **M3** — Scout daily digest + email, one-click commission.
- **M4** — Recruiter logic, hire/fire surfacing, EIC feedback writing back to persona files, manual persona editor.
- **M5** — Polish: chart library expansion, more data sources, archive search, audit log UI.

Stop at the end of each milestone, deploy, demo before continuing.

---

## 11. Conventions

Type-hint everything (mypy strict + TS strict). No silent failures. Idempotent stages. Markdown is the lingua franca. Tools, not chains (agents hand off via state machine). No mocking real data. Local-first dev. Secrets in `.env`. Ask before architectural changes.

---

## 12. First session

1. Read this file. Acknowledge in own words.
2. Confirm assumptions, surface ambiguity.
3. Propose first concrete M1 deliverable.
4. Wait for go.
5. Build.

Project name: **Forte Research**. Logo + Hillgate template in `assets/`.
