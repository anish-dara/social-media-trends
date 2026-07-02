# TrendRadar — Phases 1–3 + experimental prediction

Daily capture + categorization of trending TikTok hashtags, with velocity/
acceleration/lifecycle-stage metrics computed from the accumulating history,
persistence-ranked time-window views, retailer product suggestions derived
from the trends, a top-creators view, an experimental forward-growth predictor
trained on real trajectory data, and a read-only dashboard. See
`PROJECT_PLAN.md` for the full thesis, and `CLAUDE_CODE_PHASE1.md` /
`CLAUDE_CODE_PHASE2.md` / `CLAUDE_CODE_PHASE3.md` for the per-phase build specs
(collect → measure → insight & products). The prediction layer is an early
prototype — see "Prediction" below for its honest status.

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
- **Tier 2 — named product/brand mentions (built; manual step).** Mines the
  top videos' captions per hashtag for concrete product/brand mentions (e.g.
  "people are using Stanley Quencher tumblers"). Captions come via a two-hop
  path, since no single endpoint gives them anonymously:
  1. **`GetHashtagDetail`** (login-gated) returns a hashtag's top **video
     URLs**. Its `videoList` carries URLs + stats but, confirmed by a
     logged-in capture, **no caption text**.
  2. **TikTok's public oEmbed endpoint** (`/oembed?url=<videoURL>`, no login,
     no key) turns each video URL into its caption (the `title` field).

  Because step 1 needs a logged-in session and those cookies expire in ~3
  days, Tier 2 is **not in the unattended cron** — it's a manual local step,
  `python -m src.enrich_tier2`, run when you have a fresh login. It reads
  `TIKTOK_COOKIE` / `TIKTOK_CSRF` from `.env` (gitignored — cookies never go
  in git), enriches the top ~20 hashtags by persistence, and writes
  `named_products` to `trend_products`. Tier 1 and the daily cron are
  unaffected if it's never run. The no-login halves (oEmbed fetch + LLM
  extraction) are verified live; the login half runs against the confirmed
  `GetHashtagDetail` shape.

### Deferred

- **Tier 3 — real TikTok Shop SKUs with sales signal.** The highest-value
  output (actual products tied to real demand) needs a paid product-data
  source (EchoTik etc. — TikTok exposes no public sales API). This is the
  budget ask to bring to the lead, with the live demo as leverage. The
  `ECHOTIK_API_KEY` slot is in `.env.example`; the client is intentionally
  not built until a real endpoint sample confirms its shape.

## Platforms (Phase 4)

TrendRadar now ingests **two platforms** behind one generic shape:

- **TikTok** — trending hashtags (anonymous Creative Center scrape; see below).
- **YouTube** — trending videos via the official **YouTube Data API v3**
  (`chart=mostPopular`, ~1 quota unit/day of a free 10k budget — no scraping,
  no login, no bot challenge). Needs `YOUTUBE_API_KEY` in `.env`. All YouTube
  logic lives in `src/youtube.py`.

Both map onto a shared schema: `trends.platform` distinguishes them, and
`snapshots.primary_metric` is the platform-agnostic number the velocity/stage
and prediction engines difference over (TikTok video_count, YouTube view
count). Existing TikTok rows were migrated in place and backfilled — verified
identical before/after (no-regression gate). Sources are **isolated in the
pipeline**: YouTube failing (quota) or TikTok drifting logs an error and the
run continues with whatever succeeded.

**Cross-platform rule (enforced in the dashboard):** lifecycle **stage** and
**velocity** are relative to each trend itself and *are* comparable across
platforms — "rising" means the same thing everywhere. Raw `primary_metric`
magnitudes are **not** comparable (YouTube views ≫ TikTok video counts,
different units), so the UI never ranks across platforms by raw metric.

The predictor stays TikTok-only for now: it needs the 7-point popularityCurve,
which is a TikTok field YouTube doesn't provide.

### Cross-platform linking

`src/link.py` uses the LLM (same `claude-sonnet-4-6` stack — deliberately no
LangChain/vector store, which would be overkill at ~60 items/day and weaker on
cryptic hashtags where world knowledge beats token similarity) to group
trends that refer to the same real-world topic across platforms — e.g. a
TikTok `#gta6` and a YouTube "GTA 6 trailer" video. Only groups spanning ≥2
platforms are kept (the "trending everywhere" subset), stored in `trend_links`
and shown in the dashboard's "Across platforms" tab. Runs as an isolated daily
step. **Expect it to be sparse** — TikTok breakout hashtags are often niche and
don't overlap YouTube; the linker returns 0 rather than forcing weak matches
(verified: it correctly links `#gta6` ↔ a GTA 6 video in a controlled test, and
correctly finds nothing when the day's real trends don't overlap).

## Prediction (Phase 5, experimental prototype)

Every hashtag response carries a real **7-day `popularityCurve`** (normalized
0–100), stored in `snapshots.raw_json` since Phase 1. That's real per-hashtag
trajectory history, so the predictor trains on **real captured data — never
fabricated data.** (Synthetic curves appear only in `tests/test_trajectory.py`,
to exercise the feature math — the legitimate use of synthetic data: testing
plumbing, not training a model.)

- `src/trajectory.py` — extracts stored curves into two real datasets:
  a *shape* set (early curve → its own tail) and the *forward* set (a day's
  early-curve features → did the hashtag's `video_count` actually grow by its
  next capture). Pure feature functions, unit-tested.
- `src/predict.py` — a deliberately simple, class-balanced logistic-regression
  model. Every metric is **out-of-sample (5-fold cross-validated) and compared
  to a majority-class baseline**, so nothing flatters itself.
- Runs as an isolated step in the daily pipeline (after products), writing a
  growth probability per trend to the `predictions` table — persisted so
  predictions can later be scored against what actually happened.

**Honest status — read before trusting it.** At current volume the *forward*
model (the real target) scores ROC-AUC ≈ 0.6: barely above a coin flip, and
below the majority-accuracy baseline. **It is not yet a reliable predictor.**
The *shape* model scores well (AUC ≈ 0.88) but that's the weaker,
semi-self-fulfilling task and mainly validates that the features and pipeline
are correct. The whole point of building it now is that it improves
automatically as the cron accumulates history and the forward dataset grows —
the machine is real and runs on real data; it just needs more of it. The
dashboard's "Prediction (experimental)" tab shows the live metrics next to the
predictions so the numbers are never oversold.

## Architecture

- `src/tiktok_client.py` — every TikTok Creative Center request. If TikTok
  changes an endpoint, this is the one file to fix.
- `src/youtube.py` — every YouTube Data API request (trending videos).
- `src/categorize.py` — every Anthropic API call (topic classification via
  `claude-sonnet-4-6`), now with an optional native category hint per item.
- `src/normalize.py` — cleans raw client output into DB-ready records.
- `src/db.py` — Neon Postgres: upsert trend identities, append dated snapshots.
- `src/metrics.py` — pure velocity/acceleration/stage math, no DB access.
- `src/compute_metrics.py` — loads snapshot history, runs `metrics.py`,
  upserts the `metrics` table.
- `src/products.py` — Tier 1 retail-category inference + Tier 2 named-product
  extraction (LLM), reused `claude-sonnet-4-6`.
- `src/enrich_tier2.py` — manual Tier 2 entry point (login-fed, runs outside
  the cron); see the Tier 2 note above.
- `src/db.py` — also holds `get_window_trends()` (persistence windows),
  `upsert_trend_products()`, `get_top_influencers()`, and `upsert_prediction()`.
- `src/link.py` — LLM cross-platform trend linker (topics trending on 2+
  platforms).
- `src/trajectory.py` — extracts real popularity curves into prediction
  datasets; pure feature functions.
- `src/predict.py` — forward-growth model + honest cross-validated evaluation.
- `src/pipeline.py` — orchestrates one daily run end to end: ingest each
  platform (isolated per source) → compute metrics → Tier 1 products → growth
  prediction (each added step is wrapped/isolated so its failure can't break
  the rest).
- `dashboard/app.py` — read-only Streamlit view: Trends, Retailer view,
  Influencers, Across platforms, Prediction (experimental), About.

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
