# TrendRadar — Phase 1 + 2

Daily capture + categorization of trending TikTok hashtags, with velocity/
acceleration/lifecycle-stage metrics computed from the accumulating history,
and a read-only dashboard. See `PROJECT_PLAN.md` for the full thesis,
`CLAUDE_CODE_PHASE1.md` for the collect/categorize/dashboard build spec, and
`CLAUDE_CODE_PHASE2.md` for the metrics build spec.

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
.venv/bin/python3 -m src.pipeline          # one daily run: fetch, categorize, store, compute metrics
.venv/bin/python3 -m pytest tests/         # pure-function metric tests (no DB touched)
.venv/bin/streamlit run dashboard/app.py   # read-only dashboard
```

Reruns for the same day are idempotent (`UNIQUE (trend_id, captured_date)` in
`schema.sql`, and `UNIQUE (trend_id, computed_date)` on `metrics`, mean a
rerun updates today's rows instead of duplicating them).

`captured_date`/`computed_date` always default to **UTC**, not local time —
see "Known limitations" below for why this matters.

## How the daily cron works

`.github/workflows/daily.yml` runs `python -m src.pipeline` every day at
06:00 UTC, plus on manual `workflow_dispatch`. It needs two repo secrets:
`NEON_DATABASE_URL` and `ANTHROPIC_API_KEY` (Settings → Secrets and
variables → Actions). `pipeline.py` calls the metrics compute step itself
after writing snapshots, so there's no separate cron or secret for Phase 2.

## Trend metrics (Phase 2)

After each day's snapshots are written, `compute_metrics.py` reads every
trend's full snapshot history and writes one `metrics` row per trend per day:

- **Smoothing** — trailing 3-snapshot moving average of `video_count`, to
  damp day-to-day noise before differencing.
- **Velocity** — relative growth rate of the smoothed count, % per day,
  normalized by the actual day gap between snapshots.
- **Acceleration** — change in velocity between the two most recent readings
  (is growth itself speeding up or slowing down?).
- **Stage** — a deterministic, rule-based label, evaluated top to bottom,
  first match wins:
  - `new` — fewer than 4 snapshots; too little history to say anything.
  - `dormant` — no snapshot in the last 3+ days (fell off the daily list).
  - `declining` — velocity ≤ -5%/day.
  - `cresting` — still growing but sharply decelerating, or flat/plateaued.
  - `rising` — velocity ≥ +5%/day.

All thresholds are named constants at the top of `src/metrics.py`. The
metric functions themselves are pure (no DB access) and unit-tested against
synthetic curves in `tests/test_metrics.py` — real history is still only a
few days deep, so live output mostly reads `new` for now; that's expected,
not a bug, and resolves itself as daily runs accumulate.

## Architecture

- `src/tiktok_client.py` — every TikTok Creative Center request. If TikTok
  changes an endpoint, this is the one file to fix.
- `src/categorize.py` — every Anthropic API call (topic classification via
  `claude-sonnet-4-6`).
- `src/normalize.py` — cleans raw client output into DB-ready records.
- `src/db.py` — Neon Postgres: upsert trend identities, append dated snapshots.
- `src/metrics.py` — pure velocity/acceleration/stage math, no DB access.
- `src/compute_metrics.py` — loads snapshot history, runs `metrics.py`,
  upserts the `metrics` table.
- `src/pipeline.py` — orchestrates one daily run end to end: ingest, then
  compute metrics.
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
- **Always use UTC for `captured_date`/`computed_date`, never local time.**
  An earlier version of `pipeline.py` defaulted to `datetime.date.today()`
  (local time). Running it manually from a non-UTC machine near midnight UTC
  produced a date one day off from the same-moment GitHub Actions run (which
  runs in UTC), corrupting that day's snapshot row via the idempotent
  upsert. Fixed to use `datetime.datetime.now(datetime.timezone.utc).date()`
  everywhere — if you add a new entry point that writes `captured_date` or
  `computed_date`, use the same pattern.
