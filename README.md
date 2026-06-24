# TrendRadar — Phase 1

Daily capture + categorization of trending TikTok hashtags, with a read-only
dashboard. See `PROJECT_PLAN.md` for the full thesis and `CLAUDE_CODE_PHASE1.md`
for the build spec this implements.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # fill in NEON_DATABASE_URL and ANTHROPIC_API_KEY
.venv/bin/python3 -c "import psycopg, os, dotenv; dotenv.load_dotenv(); psycopg.connect(os.environ['NEON_DATABASE_URL']).cursor().execute(open('schema.sql').read())"  # one-time: apply schema.sql
```

(Or just run the equivalent inline with `psql $NEON_DATABASE_URL -f schema.sql`
if you have `psql` installed.)

## Running locally

```bash
.venv/bin/python3 -m src.pipeline          # one daily run: fetch, categorize, store
.venv/bin/streamlit run dashboard/app.py   # read-only dashboard
```

Reruns for the same day are idempotent (`UNIQUE (trend_id, captured_date)` in
`schema.sql` means a rerun updates today's row instead of duplicating it).

## How the daily cron works

`.github/workflows/daily.yml` runs `python -m src.pipeline` every day at
06:00 UTC, plus on manual `workflow_dispatch`. It needs two repo secrets:
`NEON_DATABASE_URL` and `ANTHROPIC_API_KEY` (Settings → Secrets and
variables → Actions).

## Architecture

- `src/tiktok_client.py` — every TikTok Creative Center request. If TikTok
  changes an endpoint, this is the one file to fix.
- `src/categorize.py` — every Anthropic API call (topic classification via
  `claude-sonnet-4-6`).
- `src/normalize.py` — cleans raw client output into DB-ready records.
- `src/db.py` — Neon Postgres: upsert trend identities, append dated snapshots.
- `src/pipeline.py` — orchestrates one daily run end to end.
- `dashboard/app.py` — read-only Streamlit view.

## Known limitations (read before extending)

These are real deviations from the original build spec, discovered by testing
the live API rather than assumed — see commit history and conversation
context for how each was found.

- **No trending sounds.** TikTok Creative Center's "Songs" trends tab has no
  data right now — it shows "coming soon" in the product itself (confirmed
  2026-06-23). `tiktok_client.py` has no `get_trending_sounds` yet. Revisit
  once TikTok ships it.
- **Hashtag list is assembled, not TikTok's literal top-100.** The full
  ranked top-100 hashtag table requires a logged-in TikTok Ads session
  (confirmed by testing logged out). This project deliberately stays
  anonymous (see `PROJECT_PLAN.md` §5 — "no login required" is a locked
  decision), so `get_trending_hashtags` instead loops the anonymous
  "breakout" endpoint across 19 confirmed-valid `industryID` filter values
  and dedupes the results. This yields TikTok's own per-industry rising-trend
  signal rather than a flat popularity ranking — arguably a better fit for
  this project's actual goal of catching trends early.
- **Demographics are always `unknown`.** The per-hashtag audience/age
  endpoint (`GetHashtagDetail`) is also gated behind login (`StatusCode
  38001001 "InvalidLogin"` with no cookies). `get_hashtag_demographics`
  always returns `None`. The `demographics` JSONB column stays in the schema
  for whenever a login-free path exists.
- **Endpoints are undocumented.** `https://ads.tiktok.com/CreativeOne/KnowledgeAPI/GetHashtagList`
  was confirmed live via DevTools, not from any official documentation. It
  can drift without notice — if the daily run starts failing or returning
  empty, check this endpoint first.
