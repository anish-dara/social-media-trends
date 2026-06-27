# TrendRadar — Phases 1–3

Daily capture + categorization of trending TikTok hashtags, with velocity/
acceleration/lifecycle-stage metrics computed from the accumulating history,
persistence-ranked time-window views, retailer product suggestions derived
from the trends, and a read-only dashboard. See `PROJECT_PLAN.md` for the full
thesis, and `CLAUDE_CODE_PHASE1.md` / `CLAUDE_CODE_PHASE2.md` /
`CLAUDE_CODE_PHASE3.md` for the per-phase build specs (collect → measure →
insight & products).

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

## Insight & products (Phase 3)

This phase reads value out of the accumulated data rather than collecting more.

### Persistence windows (Workstream A)

`db.get_window_trends(window_days, ...)` returns the **union** of trends that
appeared at least once in the last N days, ranked by **persistence** =
`days_present` (how many distinct days in the window the trend showed up). A
trend that appears 6 of 7 days is durable; one that appeared once was noise.
The dashboard's "Trends" tab exposes this with a Daily / Weekly / Monthly /
Past-N-days / Date-range window toggle, on-screen filters (category, type,
stage), and sortable columns.

**Cold-start honesty:** the persistence denominator is
`min(window_days, days actually collected so far)`, shown in the UI as
"X/Y days". The system has only been collecting for a short time, so a 7-day
window with 5 days of data reads "5/5", never "5/7" — trends aren't penalized
for days before collection began.

### Products from trends (Workstream B)

A daily step turns trends into a retailer deliverable, written to the
`trend_products` table and surfaced in the dashboard's "Retailer view" tab.
Built as a ladder:

- **Tier 1 — inferred retail categories (live).** `products.py` maps each top
  trend (top ~50 by persistence, dormant excluded) to retail product
  categories a store could stock, plus a one-line rationale, via one batched
  `claude-sonnet-4-6` call. E.g. `#ecoflow` → portable power stations / solar
  panels / emergency kits. Trends with no retail angle correctly return empty
  rather than forcing a stretch. Runs in the daily pipeline, isolated so an
  LLM/JSON failure can't break ingestion or metrics.
- **Tier 2 — named product/brand mentions (blocked, not built).** Would mine
  the top videos' captions per hashtag for concrete product/brand mentions.
  The only source for that caption text is TikTok's `GetHashtagDetail`
  endpoint, which is **login-gated** (returns `InvalidLogin` anonymously), and
  the public TikTok tag pages are bot-challenge-walled. The data we collect
  anonymously (the hashtag list) carries no caption text. A logged-in
  Creative Center session *can* reach it, but those cookies expire in ~3 days,
  so Tier 2 can't live in the unattended daily cron — if built, it would be a
  manual, locally-run enrichment step using a fresh login, separate from the
  hands-off daily job. Deferred pending that decision or a data source.

### Deferred

- **Tier 3 — real TikTok Shop SKUs with sales signal.** The highest-value
  output (actual products tied to real demand) needs a paid product-data
  source. This is the budget ask to bring to the lead, with the live
  A + Tier 1 demo as leverage.
- **Influencer analysis per category** (original Workstream C) — a fast-follow
  once A and B land.

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
- `src/products.py` — Tier 1 retail-category inference (LLM), reused
  `claude-sonnet-4-6`; Tier 2 (named products) deferred — see above.
- `src/db.py` — also holds `get_window_trends()` (persistence windows) and
  `upsert_trend_products()`.
- `src/pipeline.py` — orchestrates one daily run end to end: ingest → compute
  metrics → infer Tier 1 products (the product step is wrapped/isolated).
- `dashboard/app.py` — read-only Streamlit view: Trends, Retailer view, About.

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
