# TrendRadar — Project Plan

*Working name. A daily TikTok trend-intelligence pipeline that captures, categorizes, and tracks trends over time — built to eventually predict which trends will explode and how long a product riding them will sell.*

**Status:** Phase 1 scoping, locked. Internship project. Heavy AI use authorized.
**Owner:** Anish · **Build agent:** Claude Code (Opus)

---

## 1. The bet

The thesis is simple: **trends are predictable if you watch them as time-series instead of snapshots.** Everyone can see what's trending *today*. Almost nobody has clean day-over-day history of *how fast* each trend is rising, where on its lifecycle it sits, and whether it's still accelerating or already cresting. If you own that history, you can do two things competitors can't: tell a store **what's about to pop** before it's obvious, and estimate **how long a product will keep selling** before the trend dies.

The entire value of this project is the longitudinal dataset. A one-time snapshot is worthless; the 60th consecutive daily snapshot is the asset. That's why the "consistent daily work" requirement isn't a side note — it *is* the product.

## 2. What we're building

A pipeline that runs once a day, unattended, and does four things:

1. **Capture** — pull the top trending hashtags and sounds from TikTok (the trend unit), plus their stats.
2. **Cleanse** — normalize into a consistent schema, dedupe against history, store a dated snapshot.
3. **Categorize** — tag each trend into a topic (music, fitness, food, lifestyle, beauty, tech, etc.) and attach age-demographic data.
4. **Surface** — a thin dashboard showing what's trending, by category, with the day-over-day movement starting to accumulate.

The full value chain is: *capture trends → track velocity → predict explosions → estimate product sell-through window → recommend to partner stores.* Phase 1 builds only the first link, deliberately. The rest is impossible until the data exists.

## 3. Core principle: the signal is in the derivative

This shapes the schema, so it's stated up front. A hashtag sitting at 1M views that hasn't moved in a week is **dying**. A hashtag at 50K views that doubled yesterday is **exploding**. Absolute popularity tells you almost nothing about the future; the *rate of change* tells you everything.

So the system must store **dated snapshots** of every trend's metrics, not just current values. Velocity (Δ per day) and acceleration (Δ velocity) get computed later from that history. Phase 1 doesn't compute them — but it must capture the raw daily numbers that make them computable, or the whole roadmap is dead on arrival.

## 4. Decisions locked

These were open; they're now closed. No re-litigating in phase 1.

| Decision | Choice | Why |
|---|---|---|
| First platform | **TikTok** | Where consumer-product trends actually originate and convert ("TikTok made me buy it"). |
| Data access | **DIY scrape** (no paid provider yet) | Zero budget dependency to start. Revisit if it proves too brittle. |
| Trend unit | **Hashtag + Sound** | The two units that actually define a TikTok trend; products link to them later. |
| Prediction model | **Deferred to Phase 3** | Cannot predict explosions on day 1 with zero history. Phase 1 accumulates the history. |
| Build agent model | **Claude Code on Opus** | Complex multi-file pipeline; use the strongest model, not Sonnet. |

## 5. Data source: TikTok Creative Center

The DIY decision raises an obvious risk — TikTok's main app is hostile to scrapers (CAPTCHA, dynamic loading, IP bans). **We don't scrape the main app.** We scrape **TikTok Creative Center** (`ads.tiktok.com/business/creativecenter`), TikTok's own public-facing trend dashboard built for advertisers.

Why this is the right DIY target:

- **No login or account required.** It's a public trends product.
- **Backend JSON API.** The UI is backed by JSON endpoints you can call directly — no need to render and parse pages.
- **Exactly the data we want:** trending hashtags (video count, view totals, trend direction: rising/falling/stable), trending sounds (usage counts, creator, play URL), filterable by country and time window (7 / 30 / 120 days).
- **Per-hashtag detail includes audience age demographics** — which means age-group segmentation comes from the source, not from guessing. This quietly satisfies the "categorize by age" requirement.

Honest caveats, baked into the plan:

- Creative Center data is **aggregated for advertisers and can lag the main app by a few hours.** Acceptable — we run daily, not real-time.
- ByteDance still **rate-limits / IP-challenges** aggressive callers. Mitigation: low request volume (we want ~20–30 items per category per day, not thousands), polite delays, retry-with-backoff, and a clean User-Agent. If we get blocked consistently, the fallback is a residential proxy or a cheap paid endpoint — but we try clean first.
- Endpoints are **undocumented and can change.** Mitigation: isolate all TikTok-specific calls behind one module so a breakage is a one-file fix, and log raw responses so we can diagnose drift fast.

## 6. The trend unit, defined

A **trend** in phase 1 is a row keyed by either a **hashtag** or a **sound**, captured on a specific **date**. Each daily capture is a snapshot. Over time, the same hashtag/sound accrues many dated snapshots — that sequence is the time-series.

We do **not** try to cluster hashtags+sounds into higher-level "themes" yet. One hashtag = one trend, one sound = one trend. Clustering is a Phase 2+ refinement once we see how the raw units behave.

## 7. Data model (the crux)

Two tables. Keep it boring and readable.

**`trends`** — one row per unique trend (the identity).
- `id` (pk)
- `type` (`hashtag` | `sound`)
- `name` (e.g. `#cleantok`, or sound title)
- `tiktok_id` (Creative Center's own id, for joining details)
- `category` (filled by categorizer: music / fitness / food / lifestyle / beauty / tech / ...)
- `first_seen_date`
- `country` (start with one market, e.g. US)

**`snapshots`** — one row per trend per day (the time-series). This is the asset.
- `id` (pk)
- `trend_id` (fk → trends)
- `captured_date`
- `rank` (its position in that day's top list)
- `video_count`
- `view_count`
- `trend_direction` (TikTok's own rising/falling/stable label)
- `raw_json` (store the full raw response — cheap insurance against schema drift and future features)

Plus an optional **`demographics`** table or JSON column for the per-hashtag age/country breakdown when we pull detail.

The design rule: **never overwrite a metric — always append a new dated snapshot.** That single rule is what makes velocity computable later.

## 8. Categorization

Two separate jobs, different methods.

**Topic** (music/fitness/food/etc.) — zero-shot classification with an LLM. Feed the hashtag/sound name + any associated caption text + related hashtags into a prompt that returns one category from a fixed taxonomy. Cheap, accurate, no training data needed, and AI use is explicitly authorized. The taxonomy is a fixed list we define up front so categories stay consistent across days.

**Age group** — taken from Creative Center's per-hashtag audience demographics where available. This is real source data, not inference. Where a given trend has no demographic breakdown, we mark it `unknown` rather than fabricate one. Be honest about coverage; don't pretend every trend has clean age data.

## 9. Architecture (daily pipeline)

```
[GitHub Actions cron, 1×/day]
        │
        ▼
[tiktok_client.py] ── calls Creative Center JSON endpoints
        │              (trending hashtags, trending sounds, hashtag detail)
        ▼
[normalize.py] ─────── clean + dedupe against existing trends
        │
        ▼
[categorize.py] ────── LLM topic tag + attach demographics
        │
        ▼
[Neon Postgres] ────── upsert trends, append dated snapshots
        │
        ▼
[dashboard] ────────── read-only view of today + accumulating history
```

Everything TikTok-specific lives in `tiktok_client.py`. Everything LLM-specific lives in `categorize.py`. If TikTok changes, you fix one file. If categorization needs tuning, you touch one file.

## 10. Tech stack

- **Language:** Python (readable, every scraping/data lib lives here).
- **Storage:** Postgres on **Neon** (already used; serverless, free tier fine for this volume).
- **Scheduler:** **GitHub Actions** cron — free, version-controlled, no server to babysit. The daily run is a workflow.
- **Categorization:** Claude API (Sonnet is fine for classification; cheap and fast).
- **Dashboard:** **Streamlit** for phase 1 — fastest path to a working view. Migrate to Next.js later only if it needs to be a real product surface.
- **Build agent:** Claude Code on **Opus**.

Deliberately boring. No Kafka, no orchestration framework, no microservices. One repo, one cron, two tables.

## 11. Roadmap & phase gates

| Phase | Goal | Gate to advance |
|---|---|---|
| **1 — Collect** | Reliable daily ingestion + categorization + thin dashboard | 14+ consecutive clean daily runs; ≥20 trends/category/day captured with no manual touch |
| **2 — Measure** | Compute velocity & acceleration from accumulated history; flag rising/cresting | Enough history (≥3–4 weeks) for the derivatives to be meaningful |
| **3 — Predict** | Model "what will explode" from early velocity/engagement signals | A labeled set of past trends that did/didn't pop, to validate against |
| **4 — Monetize** | Estimate product sell-through window; recommend to partner stores | A working predictor + a real store partner to pilot with |

Each gate is a real stop. Don't start Phase 2 math on 3 days of data; don't pitch a store before the predictor works.

## 12. Phase 1 deliverables & definition of done

**Deliverables**
1. A repo with the pipeline (`tiktok_client`, `normalize`, `categorize`, `db`).
2. A GitHub Actions workflow that runs it daily and commits/logs the result.
3. Neon database populated and growing daily.
4. A Streamlit dashboard: today's top trends by category, with age breakdown where available, and a "days tracked" counter per trend.
5. A short README so anyone (and future-you) can run it.

**Definition of done** — Phase 1 is finished when:
- The pipeline runs **unattended on a schedule** and survives a week without manual intervention.
- Each run captures **≥20–30 hashtags and sounds**, categorized, with snapshots appended (never overwritten).
- The dashboard shows the data and the **history is visibly accumulating** day over day.
- A single TikTok endpoint change would be a **one-file fix**.

That's it. No velocity, no prediction, no store outreach. Resist scope creep — the win condition for phase 1 is *boring reliability*, because that's what every later phase is built on.

## 13. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Creative Center blocks/rate-limits the scraper | Low daily volume, backoff + retry, polite headers; raw-response logging to diagnose; proxy or cheap paid endpoint as fallback |
| Undocumented endpoints change | All TikTok calls behind one module; store `raw_json`; log everything |
| "Consistency" requirement slips (the gig's kill switch) | The cron *is* the consistency — once it runs daily, you're consistent by construction, not by willpower |
| ToS / legal exposure from scraping | Creative Center is public, no login, low volume, public aggregate data — lowest-risk DIY posture; flag to your manager that this is the chosen approach so it's a shared decision |
| Age-demographic data is patchy | Use source data where present, mark `unknown` otherwise; never fabricate |
| Building the model too early | Hard phase gate; Phase 1 explicitly forbids it |

## 14. Locked parameters

All open questions are closed. Phase 1 builds against these exact values — no ambiguity left for the build agent.

| Parameter | Locked value |
|---|---|
| Market | **US only** (`country_code=US`). Expand to other markets in a later phase. |
| Topic taxonomy | **`music`, `fitness`, `food`, `beauty`, `fashion`, `lifestyle`, `tech`, `gaming`, `finance`, `other`** — fixed list, applied identically every day. |
| Trend window | **7 days** for the daily list pull |
| Items captured | **30 hashtags + 30 sounds** per day; demographics for the **top 20 hashtags** |
| Daily cron | **06:00 UTC** (`0 6 * * *`) so each "day" is well-defined and consistent |
| Categorizer model | **`claude-sonnet-4-6`** (build agent runs on Opus) |

The phase-1 build spec — `CLAUDE_CODE_PHASE1.md` — is written and reflects every value above.
