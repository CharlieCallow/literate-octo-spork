# Forte Research

Multi-agent thematic research reports. See [PROJECT.md](./PROJECT.md) for the spec.

## Stack

- **Backend.** FastAPI (Python 3.11+), SQLite, Anthropic SDK.
- **PDF.** Playwright (Chromium) + Jinja2 + matplotlib.
- **Frontend.** Next.js 15 (App Router).
- **Queue.** Postgres `jobs` table polled by a Python worker (SQLite for M1).

No Docker required. Runs natively on Windows, macOS, Linux.

## Prereqs

Install once:

- **Python 3.11+** — https://www.python.org/downloads/ (during install, tick *Add Python to PATH*).
- **Node.js 20+** — https://nodejs.org/ (LTS installer).

## Setup (Windows)

```powershell
git clone <repo-url>
cd literate-octo-spork

# 1. Install dependencies (one-time, ~5 min — downloads Chromium)
.\install.ps1

# 2. Configure secrets
Copy-Item .env.example .env
# edit .env — fill in ANTHROPIC_API_KEY, FRED_API_KEY, DASHBOARD_PASSWORD_HASH

# 3. Generate a password hash
python -c "import bcrypt; print(bcrypt.hashpw(b'CHANGE-ME', bcrypt.gensalt()).decode())"
# paste the $2b$... output into DASHBOARD_PASSWORD_HASH= in .env

# 4. Run
.\start.ps1
```

`start.ps1` opens three PowerShell windows: API on `:8000`, worker, dashboard on `:3000`. Close them to stop.

## Setup (macOS / Linux)

```bash
git clone <repo-url>
cd literate-octo-spork

python3 -m pip install -e .
python3 -m playwright install chromium
(cd web && npm install --legacy-peer-deps)

cp .env.example .env
# edit .env

# password hash
python3 -c "import bcrypt; print(bcrypt.hashpw(b'CHANGE-ME', bcrypt.gensalt()).decode())"

# in three terminals:
python3 -m uvicorn api.main:app --reload --port 8000
python3 -m api.workflow.worker
(cd web && npm run dev)
```

## Smoke test (no API call needed)

Confirms the rendering layer works without burning API credits:

```bash
python -m scripts.smoke_render
# -> smoke-out/report.pdf, smoke-out/charts/*.png
```

Open `smoke-out/report.pdf` — you should see a multi-page PDF with the Forte cover, four contributors, three charts inline, navy callouts, and a Sources section.

## Tests + lint

```bash
pip install -e ".[dev]"
pytest        # ~30 unit tests, ~3s
ruff check    # style + import order
```

CI runs the same on every PR (`.github/workflows/ci.yml`) plus a Next.js typecheck + build.

## Generate a report

1. Open http://localhost:3000
2. Sign in (`admin` / your password)
3. **New report** → type a theme → **Commission**
4. Watch progress on the home page (auto-polls every 3s)

Each report runs through stages:

`queued → brief → research → charts → draft → edit → render → feedback → done`

PDF lands embedded in the dashboard and on disk at `reports/<id>/report.pdf`.

## Edit + re-render an existing report

Each finished report writes an editable `reports/<id>/draft.md` alongside
the PDF. Open it in any text editor, fix the typo / star count / sentence,
and re-render — no LLM calls, no chart regeneration:

```bash
make render REPORT=reports/42
# or:
python -m scripts.render_report reports/42
```

`draft.md` is the single source of truth: YAML frontmatter for cover
metadata (title, contributors, positions, sources, hide flags) and a
markdown body for prose. Charts are referenced by filename
(`[chart: rates.png]`) and resolved against `reports/<id>/charts/`.

## Daily Scout digest

Scout (the morning-scan agent) can run automatically every day. Toggle via `.env`:

```
SCOUT_AUTO_RUN=true
SCOUT_DAILY_TIME=07:00            # local HH:MM
RESEND_API_KEY=re_...              # optional; leave blank to skip email
SCOUT_DIGEST_EMAIL=you@you.com     # destination for the daily digest
```

The worker checks the schedule on each idle tick and fires `run_scout()` once per day past the configured time. If `RESEND_API_KEY` is configured the digest also lands in your inbox; otherwise it's just available at `/inbox` in the dashboard.

You can trigger a digest manually any time with the **Run Scout now** button on the Inbox page or:

```bash
python -m scripts.scout_now
```

## Cost guardrails

Hard caps in `.env`:

- `COST_PER_REPORT_USD=1.00` — report aborts if exceeded
- `COST_PER_DAY_USD=5.00` — daily account-wide cap

Per-call cost tracked in the audit log; cumulative cost shown on the dashboard.

## Layout

See [PROJECT.md §4](./PROJECT.md) and [CLAUDE.md](./CLAUDE.md). Briefly:

- `api/` — backend
- `api/agents/` — persona-driven agents (EIC, Macro, Equity, Data&Charts, Scout, Recruiter)
- `api/workflow/` — explicit state machine + worker
- `api/render/` — PDF + chart rendering, Forte styles
- `api/data/` — free data sources (FRED only in M1)
- `web/` — Next.js dashboard
- `team/` — persona markdown files
- `reports/<id>/` — per-report working dir
- `assets/` — Forte logo + Hillgate template
- `scripts/` — smoke test

## Deploy (Railway + Vercel)

See [DEPLOY.md](./DEPLOY.md) for the 30-minute walkthrough. tl;dr: Railway hosts
the API + worker + Postgres; Vercel hosts the Next.js dashboard. Total recurring
cost ~$5/month plus per-report Anthropic spend.

## Reset

```powershell
# nuke local data
Remove-Item forte.db, reports -Recurse -Force -ErrorAction SilentlyContinue
```
