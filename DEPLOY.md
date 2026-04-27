# Deploy: Railway + Vercel

About 30 minutes end-to-end. The API and worker live on Railway with a managed
Postgres. The Next.js dashboard lives on Vercel.

## 1. Push the repo to GitHub

If you haven't already:

```bash
gh repo create your-username/forte-research --private --source=. --push
```

Both Railway and Vercel pick up changes from GitHub automatically on each push.

## 2. Railway: API + worker + Postgres

1. Sign up at https://railway.app (Hobby tier is $5/month, fine for this).
2. **New Project** -> **Deploy from GitHub repo** -> pick the repo.
3. Railway auto-detects Python via Nixpacks. It'll build but not yet have a
   start command. We're going to set that explicitly.

### 2a. Add Postgres

1. In your project: **+ New** -> **Database** -> **PostgreSQL**.
2. Wait ~30 seconds for it to provision. Railway exposes a `DATABASE_URL` to
   sibling services automatically; you'll wire it through in step 2c.

### 2b. The API service

1. Click the service Railway just created from your repo (default name is the
   repo name).
2. The repo's `Procfile` declares the start command:
   `web: uvicorn api.main:app --host 0.0.0.0 --port $PORT`
   Railway's Nixpacks builder picks this up automatically.
   - **If Railway ignores it** (you'll see `No module named forte-research`
     in the logs), set it manually: **Settings** -> **Deploy** ->
     **Custom Start Command**:
     `uvicorn api.main:app --host 0.0.0.0 --port $PORT`
3. Go to **Settings** -> **Networking** -> **Generate Domain**. You'll get
   something like `https://forte-research.up.railway.app`. Note this URL.

### 2c. Environment variables on the API service

In the API service's **Variables** tab, add:

```
ANTHROPIC_API_KEY=sk-ant-...
FRED_API_KEY=...
DASHBOARD_PASSWORD_HASH=$2b$12$...        # generate locally, see below
SESSION_SECRET=<openssl rand -hex 32>

# Postgres -- Railway's "Reference" feature wires this for you:
DATABASE_URL=${{Postgres.DATABASE_URL}}

# Make the API accept cross-origin requests from your Vercel domain.
# You'll fill this in after step 3 when you have the Vercel URL.
CORS_ORIGINS=https://forte-dashboard.vercel.app

# Bundle the worker into the API process so we don't need a second service.
BUNDLE_WORKER=true

# Optional: Scout daily digest + email
SCOUT_AUTO_RUN=true
SCOUT_DAILY_TIME=07:00
RESEND_API_KEY=re_...
SCOUT_DIGEST_EMAIL=you@you.com
```

Generate the password hash locally:

```bash
python -c "import bcrypt; print(bcrypt.hashpw(b'YOUR-PASSWORD', bcrypt.gensalt()).decode())"
```

### 2d. (Optional) Two services instead of bundled

If you want the worker as a separate service:

- Don't set `BUNDLE_WORKER`.
- **+ New** -> **Empty Service** -> connect to the same repo -> **Settings**
  -> **Start Command**: `python -m api.workflow.worker`.
- Copy the same env vars to the worker service (Railway has a "copy from"
  helper). Skip `CORS_ORIGINS` (worker doesn't serve HTTP).
- Both services share the same Postgres via `DATABASE_URL=${{Postgres.DATABASE_URL}}`.

For shared filesystem (so the API can serve PDFs the worker writes), use
`BUNDLE_WORKER=true` instead -- single-service, single filesystem. Or wait for
M5 Sprint 2 (R2 storage) to remove the shared-FS dependency.

## 3. Vercel: dashboard

1. Sign up at https://vercel.com (Hobby tier is free).
2. **Add New** -> **Project** -> import your GitHub repo.
3. **Framework Preset**: Next.js. **Root Directory**: `web`. Leave build
   command + output dir as defaults.
4. **Environment Variables**:
   ```
   NEXT_PUBLIC_API_BASE=https://forte-research.up.railway.app
   ```
   (the Railway URL from step 2b).
5. **Deploy**. Wait ~2 minutes.
6. Vercel gives you a URL like `https://forte-dashboard.vercel.app`. Copy it
   back into `CORS_ORIGINS` on the Railway API service (step 2c) and redeploy
   the API.

## 4. Smoke test

Open the Vercel URL. Sign in with `admin` and the password whose hash you put
in `DASHBOARD_PASSWORD_HASH`. Commission a fast-mode report and watch it land.

## 5. After-deploy checklist

- [ ] PDF embeds in the report viewer (CORS for the API matched the Vercel URL).
- [ ] Reports complete end-to-end (API + bundled worker reach Postgres).
- [ ] `/inbox` runs the Scout (web search costs ~$0.05).
- [ ] `/team` shows recommendations after a few reports finish.

## Costs

| Service | Tier | Notes |
|---|---|---|
| Railway | Hobby ($5/mo) | API + worker + Postgres + ~1GB volume |
| Vercel | Hobby (free) | Dashboard, generous bandwidth limit |
| Anthropic | Pay-as-you-go | $0.05-1.00 per report depending on mode |
| Resend | Free | 100 emails/day, no card |
| FRED | Free | Just an API key |

## Troubleshooting

| Symptom | Look at |
|---|---|
| `No module named forte-research` on boot | Procfile not picked up. Set the Custom Start Command in Settings -> Deploy: `uvicorn api.main:app --host 0.0.0.0 --port $PORT` |
| 500 on every request | Railway API logs -- usually missing env var |
| 401 with valid password | `DASHBOARD_PASSWORD_HASH` wasn't pasted fully (cut at `$`?) |
| Dashboard shows "Failed to fetch" | `NEXT_PUBLIC_API_BASE` is wrong, or `CORS_ORIGINS` doesn't include the Vercel URL |
| Reports stuck in `queued` | Worker isn't running. With `BUNDLE_WORKER=true`, look for "spawning worker as subprocess" in API logs |
| Report stuck in `render` | Playwright Chromium download failed during build. Check Railway build logs; rebuild |
| Postgres errors | `DATABASE_URL=${{Postgres.DATABASE_URL}}` missing or pointing at wrong service |
