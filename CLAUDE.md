# CLAUDE.md

Read [PROJECT.md](./PROJECT.md) at the start of every session before writing or planning anything. It is the source of truth for what we're building, why, and how. If anything here conflicts with PROJECT.md, PROJECT.md wins.

## Working rules

- Build in milestone order (§10 of PROJECT.md). Don't skip ahead.
- Stop at the end of each milestone, push, and wait for sign-off.
- Surface ambiguity before building. Ask before architectural changes.
- No silent failures. Every error logs context and surfaces to the dashboard.
- Type-hint everything (Python: mypy strict; TS: strict mode).
- Markdown is the lingua franca. Personas, agent outputs, drafts — all markdown.
- Free data only. Cache aggressively (`data_cache` table).
- Cost caps are hard. Halt and surface a clear message if hit.
- Secrets in `.env`, never in code.
- Local dev first: `docker-compose up` should give a working stack.

## Where things live

- `api/` — FastAPI backend (agents, workflow, data, render, routes)
- `web/` — Next.js dashboard
- `team/` — persona markdown files (active roster). `team/temp/` for ad-hoc, `team/archive/` for fired
- `reports/<id>/` — per-report working dir (notes, drafts, charts, final PDF)
- `assets/` — Forte logo + Hillgate template + fonts
- `api/render/styles/forte-palette.json` — single source of truth for brand colours

## Polish backlog (when time)

- Running-report card: show elapsed time + estimated time-to-finish + estimated cost
- Use the cost estimate to surface a confirm dialog before a report kicks off
- Bump Next.js past 15.0.x for the security CVE flagged at deploy time

## Session log

- **2026-04-26** — M1 kickoff. Hillgate template + Forte logo dropped into `assets/`. Palette extracted from logo (`#302070` deep purple-navy from `FORTE` wordmark, `#5A8DA6` muted teal from `SECURITIES`). Hillgate uses `#0D1F6C` navy primary and three callout styles (green/navy/amber); structure mirrored, palette retuned to Forte.
