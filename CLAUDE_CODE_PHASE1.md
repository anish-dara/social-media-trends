# TrendRadar — Phase 1 Build Spec (for Claude Code)

> **Read `PROJECT_PLAN.md` in this repo first.** It is the shared context for everything below. This file is the executable build spec for Phase 1 only.

---

## 0. Operating instructions for you, Claude Code

- **Run on Opus.** This is a multi-file pipeline with live-endpoint reverse-engineering; use the strongest model. If you are not on Opus, stop and switch (`/model opus`) before continuing.
- **Model distinction — do not confuse these two:**
  - *You* (the build agent writing this code) run on **Opus**.
  - The pipeline's *categorization step* calls the Anthropic API with **`claude-sonnet-4-6`** (cheap, fast, fine for classification). These are different things.
- **Code style:** elementary and readable over clever. Straightforward Python, clear names, comments where a future reader would be confused. No premature abstraction, no frameworks beyond what's listed.
- **Scope discipline:** Phase 1 is *collect + categorize + dashboard*. **Do NOT build velocity, acceleration, prediction, scoring, or store-recommendation logic.** If you find yourself writing math over historical snapshots, stop — that's Phase 2/3. The win condition here is boring daily reliability.
- **Verify before you trust.** The TikTok endpoint paths below are a *starting hypothesis*, not confirmed fact. Confirm them against live traffic (§3) before building around them.
- **Work in build order (§8).** After each step, run it and show real output before moving on. Don't scaffold the whole thing blind.

## 1. What Phase 1 delivers

A pipeline that runs **once a day, unattended**, and:
1. Pulls top trending **hashtags** and **sounds** from TikTok Creative Center (US).
2. Normalizes + dedupes them.
3. Tags each with a **topic category** and attaches **age demographics** where available.
4. Appends a **dated snapshot** to Postgres (never overwrites).
5. Surfaces it all in a thin **Streamlit dashboard**.

**Definition of done:** runs on a schedule and survives a week untouched; each run captures ≥20–30 hashtags and sounds, categorized; snapshots append (never overwrite); the dashboard shows accumulating day-over-day history; a single TikTok endpoint change is a one-file fix.

## 2. Locked parameters

| Param | Value |
|---|---|
| Market | **US only** (`country_code=US`) |
| Trend window | **7 days** (`period=7`) for the daily list pull |
| Items per type per day | **30** hashtags, **30** sounds (the top list) |
| Topic taxonomy (fixed) | `music`, `fitness`, `food`, `beauty`, `fashion`, `lifestyle`, `tech`, `gaming`, `finance`, `other` |
| Cron | **06:00 UTC daily** (`0 6 * * *`) |
| Categorizer model | `claude-sonnet-4-6` |
| Demographics depth | detail/demographics pulled only for the **top 20 hashtags** (keeps request volume low) |

## 3. TikTok Creative Center — data source

We scrape **TikTok Creative Center** (`https://ads.tiktok.com/business/creativecenter/...`), the public advertiser trend dashboard. **No login, no account.** Do **not** scrape the main TikTok app.

### 3.1 Verify the live endpoints FIRST (do this before writing the client)

The Creative Center UI is backed by an internal JSON API (the `creative_radar_api`). Exact paths/params drift, so confirm them live:

1. Open `https://ads.tiktok.com/business/creativecenter/inspiration/popular/hashtag/pc/en` in a browser.
2. Open DevTools → Network → filter **XHR/Fetch**.
3. Trigger the trending-hashtags list (load/scroll). Find the JSON call that returns the hashtag list. **Copy as cURL.**
4. Repeat for the **trending sounds/songs** page, and for a single **hashtag detail** view (which returns the trend curve + audience age/country demographics).
5. From the cURLs, record the exact: base path, query params, and required headers.

### 3.2 Likely shape (hypothesis — confirm against §3.1)

Base looks like: `https://ads.tiktok.com/creative_radar_api/v1/popular_trend/...`

- **Hashtag list:** `.../hashtag/list` with params roughly `page`, `limit`, `period=7`, `country_code=US`, `sort_by=popular`
- **Sound list:** `.../sound/list` (or `/music/list`) with similar params
- **Hashtag detail (demographics):** `.../hashtag/detail` keyed by the hashtag id returned in the list

**Headers that are typically required** (confirm exact set from your cURL):
- `User-Agent` — a normal desktop browser UA
- `anonymous-user-id` — generate a UUID v4 and reuse it for the session
- `timestamp` — current epoch
- `Referer` — the Creative Center page URL
- possibly a `web-id` / `msToken` cookie or an `x-...` signing header — capture whatever your cURL shows

### 3.3 If raw endpoints fight you

Before grinding on signatures, **check for a maintained open-source Creative Center client**: search GitHub / PyPI for `tiktok creative center` / `tiktok trends scraper`. If one is recent and clean, read it and either use it or copy its request-signing approach. If everything is stale, go raw with `httpx`. (Network access to `github.com`, `raw.githubusercontent.com`, `pypi.org` is available.)

### 3.4 Scraping etiquette (non-negotiable)

- **Low volume:** 30 items/type/day + 20 detail calls. That's it. Do not paginate into the thousands.
- **Be polite:** 1–2s sleep between calls; retry with exponential backoff on 4xx/5xx (max 3 tries).
- **Log raw responses** to disk/db (`raw_json`) on every call — this is how we diagnose drift later.
- If you get consistently blocked (challenge pages / empty results), **stop and report**. Do not escalate to aggressive evasion. The documented fallback is a residential proxy or a cheap paid endpoint, which is a human decision, not yours to make.

## 4. Repository structure

```
trendradar/
├── .github/workflows/daily.yml      # cron → runs the pipeline
├── src/
│   ├── tiktok_client.py             # ALL Creative Center calls live here, nowhere else
│   ├── normalize.py                 # clean + dedupe list responses into records
│   ├── categorize.py                # Claude API topic tagging + demographics attach
│   ├── db.py                        # Neon Postgres: connect, upsert trends, insert snapshots
│   └── pipeline.py                  # orchestrates one daily run end-to-end
├── dashboard/
│   └── app.py                       # Streamlit read-only view
├── schema.sql                       # DDL (below)
├── requirements.txt
├── .env.example
└── README.md
```

**Architectural rule:** every TikTok-specific request is inside `tiktok_client.py`. Every Anthropic API call is inside `categorize.py`. A breakage in either is a one-file fix.

## 5. Database schema (`schema.sql`)

Postgres on Neon. Apply this before writing `db.py`.

```sql
CREATE TABLE IF NOT EXISTS trends (
    id              SERIAL PRIMARY KEY,
    type            TEXT NOT NULL CHECK (type IN ('hashtag', 'sound')),
    name            TEXT NOT NULL,
    tiktok_id       TEXT,                         -- Creative Center's own id, for joins/detail
    category        TEXT,                         -- filled by categorizer
    country         TEXT NOT NULL DEFAULT 'US',
    first_seen_date DATE NOT NULL,
    demographics    JSONB,                        -- latest age/country breakdown (hashtags only)
    UNIQUE (type, name, country)
);

CREATE TABLE IF NOT EXISTS snapshots (
    id              SERIAL PRIMARY KEY,
    trend_id        INTEGER NOT NULL REFERENCES trends(id),
    captured_date   DATE NOT NULL,
    rank            INTEGER,                      -- position in that day's top list
    video_count     BIGINT,
    view_count      BIGINT,
    trend_direction TEXT,                         -- TikTok's rising/falling/stable label
    raw_json        JSONB,                        -- full raw response, insurance vs drift
    UNIQUE (trend_id, captured_date)              -- one snapshot per trend per day; makes reruns idempotent
);

CREATE INDEX IF NOT EXISTS idx_snapshots_trend_date ON snapshots (trend_id, captured_date);
CREATE INDEX IF NOT EXISTS idx_trends_category ON trends (category);
```

**The one rule that matters:** never UPDATE a metric — always INSERT a new dated snapshot. The `UNIQUE (trend_id, captured_date)` constraint enforces one-per-day and makes a re-run safe (upsert/`ON CONFLICT DO NOTHING` or `DO UPDATE`).

## 6. Module contracts

**`tiktok_client.py`**
- `get_trending_hashtags(limit=30) -> list[dict]` — returns normalized-ish dicts: `name`, `tiktok_id`, `rank`, `video_count`, `view_count`, `trend_direction`, `raw`.
- `get_trending_sounds(limit=30) -> list[dict]` — same shape (sounds may lack `view_count`; that's fine, leave null).
- `get_hashtag_demographics(tiktok_id) -> dict` — returns the age/country breakdown as a dict (store as JSONB).
- One shared session with the headers from §3; built-in sleep + backoff; logs raw responses.

**`normalize.py`**
- Takes raw client dicts → clean records ready for DB. Strip/standardize names (lowercase hashtag w/ leading `#`), coerce counts to ints, drop malformed rows. Dedupe within a single day's pull.

**`categorize.py`**
- `categorize(name, type, context) -> str` — one Anthropic API call (`claude-sonnet-4-6`) that returns exactly one category from the fixed taxonomy. System prompt must say: respond with ONLY one of [the 10 categories], no other text. Validate the response is in the taxonomy; fall back to `other` if not.
- Batch where sensible to limit calls (you can categorize many names in one structured-JSON call — prompt for a JSON map of name→category, parse it).
- Attaches demographics for the top-20 hashtags via the client.

**`db.py`**
- `connect()`, `upsert_trend(record) -> trend_id` (insert or fetch existing by `UNIQUE (type,name,country)`, set `first_seen_date` on first insert, update `category`/`demographics`), `insert_snapshot(trend_id, snapshot)` (`ON CONFLICT (trend_id, captured_date)` → update, so reruns are clean).

**`pipeline.py`**
- The daily run: fetch hashtags + sounds → normalize → categorize (batch) → for top-20 hashtags fetch demographics → upsert trends → insert today's snapshots → print a summary (counts per category, total captured). Idempotent for a given date.

## 7. Daily cron (`.github/workflows/daily.yml`)

- Trigger: `schedule: cron: '0 6 * * *'` plus `workflow_dispatch` (so you can run manually).
- Steps: checkout → setup Python → `pip install -r requirements.txt` → `python -m src.pipeline`.
- Secrets (GitHub repo settings): `NEON_DATABASE_URL`, `ANTHROPIC_API_KEY`. Never hardcode; read from env.
- Note GitHub Actions cron can be delayed several minutes — irrelevant for a daily job.

## 8. Build order (do these in sequence, verify each)

1. **Scaffold** — repo structure, `requirements.txt` (`httpx`, `psycopg[binary]` or `psycopg2-binary`, `anthropic`, `streamlit`, `python-dotenv`), `.env.example` (`NEON_DATABASE_URL`, `ANTHROPIC_API_KEY`), apply `schema.sql` to Neon. Confirm tables exist.
2. **`tiktok_client` — hashtags only.** Do §3.1 discovery, implement `get_trending_hashtags`, run it, **print 30 real hashtags with counts.** Don't proceed until this returns live data.
3. **Add sounds + demographics** to the client. Print samples for both.
4. **`db.py`** — upsert + snapshot insert. Run a tiny manual insert, confirm rows in Neon.
5. **`normalize.py`** — wire client → clean records.
6. **`categorize.py`** — batch categorization via `claude-sonnet-4-6`; verify every output is in the taxonomy.
7. **`pipeline.py`** — full run once, manually. Confirm: ~30+30 trends, snapshots for today, categories populated, demographics on top-20 hashtags. **Run it twice** and confirm the second run does not duplicate (idempotency).
8. **`daily.yml`** — cron + manual dispatch. Trigger it manually once via Actions and confirm a clean run + new rows.
9. **`dashboard/app.py`** — Streamlit: today's top trends by category, age breakdown where present, and a per-trend "days tracked" counter. Read-only.
10. **`README.md`** — how to set env, run locally, run the dashboard, how the cron works, and the §3 note that TikTok endpoints can drift and live in one file.

## 9. Guardrails recap

- Endpoints are unverified until you confirm them live; isolate them in `tiktok_client.py`.
- Polite, low-volume scraping; backoff; log raw JSON; stop-and-report if blocked.
- Never overwrite metrics; one snapshot per trend per day; reruns must be idempotent.
- Secrets via env only.
- **No velocity / no prediction / no scoring.** That's a later phase. Phase 1 = reliable collection + categorization + dashboard, full stop.
