# TrendRadar — Phase 3 Build Spec (for Claude Code)

> **Read `PROJECT_PLAN.md`, `CLAUDE_CODE_PHASE1.md`, and `CLAUDE_CODE_PHASE2.md` first.** Phases 1–2 (collect TikTok + measure velocity/stage) are built and running, with daily snapshots accumulating. This file is the executable build spec for **Phase 3 — Insight & Products**, and nothing beyond it.

---

## 0. Why this phase exists

Feedback from the project lead: right now it's "just a database." That's accurate — Phases 1–2 built the *substrate* (daily snapshots + velocity/stage). This phase reads **value** out of that substrate and produces a **retailer-facing output**, with almost no new data collection required. It is the phase that turns a database into a product.

It was resequenced *ahead of* platform expansion (now Phase 4) on purpose: more platforms would mean a bigger database, not a better one. Prove insight value on TikTok first.

## 0.1 Operating instructions for you, Claude Code

- **Run on Opus.** Confirm `/model opus`.
- **Mostly works on existing data.** Workstream A (below) needs *zero* new scraping — it's queries + UI over the snapshots you already have. Build it first; it's the fastest visible win.
- **Code style:** elementary and readable.
- **Reuse, don't rebuild.** The Phase 2 metrics/stage engine and the Phase 1 categorizer are reused as-is.
- **Scope boundary — hard line:** this phase surfaces *what trends imply for retailers* and *which trends are durable*. It does **NOT** build the prediction model (Phase 5) or expand to new platforms (Phase 4).
- **Demo target:** after Workstream A + Product Tier 1 are in, the lead should see "durable trends, filterable, each with retailer product suggestions." That combination is the answer to "it's just a database." Build toward that demo.

## 1. The three workstreams (in priority order)

| Workstream | What it delivers | New data needed | Effort |
|---|---|---|---|
| **A — Persistence views + filters** | Daily/weekly/monthly trend views ranked by how consistently a trend appears, with on-screen filters and sorting | **None** (existing snapshots) | Low — do first |
| **B — Products from trends** | The retailer deliverable: what to stock, derived from trends | Tier 1: none · Tier 2: top-video text | Medium |
| **C — Influencers per category** | Who's driving each category and what they push | creator data | Later / fast-follow |

Ship A, then B-Tier 1, and demo those together. B-Tier 2 next. C and the paid product-data path (B-Tier 3) are explicitly deferred to §6.

## 2. Workstream A — Persistence views & filters (build first)

### 2.1 The persistence concept
A trend that shows up 6 of the last 7 days is real; one that appeared once was noise. The time-window views are the **union** of daily trend sets over a window, ranked by **persistence** = how many distinct days in the window the trend appeared.

- **Daily** = trends with a snapshot on the latest captured_date.
- **Weekly** = trends appearing ≥1 day in the last 7; ranked by `days_present` (out of 7), tiebreak by latest velocity.
- **Monthly** = same over 30 days.
- **Custom range / "past N days"** = same logic, arbitrary window.

The persistence count is itself the insight a retailer needs: *durable vs flash*. A trend high on the weekly list consistently showed up — exactly the union behavior the lead described.

### 2.2 Cold-start handling (important)
The system has only been collecting for a short time. **Denominator = min(window_days, days_collected_so_far).** Don't penalize a trend for not appearing on days before collection started. Show the denominator in the UI ("5/5 days" not "5/7") so early data reads honestly.

### 2.3 Query (in `db.py`)
`get_window_trends(window_days, filters) -> list[row]` returning, per trend in the window:
- trend identity (name, type, category, platform)
- `days_present` and `persistence_ratio = days_present / effective_window`
- latest snapshot's `primary_metric`, `rank`
- latest `velocity`, `acceleration`, `stage` (join `metrics`)
Order by the requested sort. Add an index on `snapshots(captured_date)` if not present.

### 2.4 Dashboard (`dashboard/app.py`)
This is the demo surface. Add:
- **Window toggle:** Daily / Weekly / Monthly / Custom date range (date pickers + "past N days").
- **Filters (on-screen, the lead asked for this explicitly):** category (the 10), trend type (hashtag/sound), stage (new/rising/cresting/declining/dormant), platform (tiktok for now).
- **Sort by:** persistence, velocity, latest metric, rank.
- **Per-trend row:** name, category, stage badge, velocity, **persistence ("X/Y days")**, sparkline (from Phase 2).
- Default weekly view sorted by persistence — instantly shows "the trends that consistently appear."

The "durable AND rising" view (filter stage=rising, sort by persistence) is the single strongest thing to show the lead.

## 3. Workstream B — Products from trends

The retailer deliverable. Built as a ladder of increasing fidelity; build Tier 1 first (it always works), then Tier 2.

### 3.1 Schema (`schema.sql`)
```sql
CREATE TABLE IF NOT EXISTS trend_products (
    id                 SERIAL PRIMARY KEY,
    trend_id           INTEGER NOT NULL REFERENCES trends(id),
    generated_date     DATE NOT NULL,
    product_categories JSONB,   -- Tier 1: inferred retail categories + rationale
    named_products     JSONB,   -- Tier 2: extracted product/brand mentions
    UNIQUE (trend_id, generated_date)
);
```

### 3.2 Tier 1 — inferred product categories (LLM reasoning, always works)
A step that maps each **active/top** trend → retail product categories a store could stock.
- Input: trend name + category + (if available) top related hashtags.
- Output (strict JSON): `{"categories": ["running shoes","athletic apparel","hydration"], "rationale": "one short line"}`.
- Example: a rising running/marathon hashtag → running shoes, athletic apparel, hydration, energy gels. No SKUs, but a retailer can act on it immediately.
- Model: `claude-sonnet-4-6`. **Batch** many trends per call (prompt for a JSON map of trend→categories) to control cost. Validate JSON; on parse failure, store empty and move on.
- **Limit scope to control cost:** only run for trends that are active (have a recent snapshot) and reasonably ranked — e.g. top ~50 by persistence or velocity. Don't infer products for dormant trends.

### 3.3 Tier 2 — named product/brand mentions (from top-video text)
For the **top ~20 hashtags** (by persistence/velocity), pull the hashtag's top videos (the Creative Center hashtag-detail response already carries top videos + captions — reuse the Phase 1 client path), and extract concrete product/brand mentions.
- Input: concatenated captions/descriptions of the top videos for one hashtag.
- Output (strict JSON): `{"named_products": [{"product": "...", "brand": "...", "mentions": N}]}`.
- This is the difference between "stock running shoes" and "people are using [specific brand]" — much stronger for the lead.
- **Degrade gracefully:** captions are often sparse or absent. If there's little text, return empty `named_products` and rely on Tier 1. Never block on it.
- Model: `claude-sonnet-4-6`.

### 3.4 Pipeline integration
Add a product step **after** metrics compute in the daily run: select top trends → Tier 1 (batch) → Tier 2 (top 20) → upsert `trend_products` for today (`ON CONFLICT (trend_id, generated_date)`). Idempotent. If the product step fails, it must not break ingestion/metrics (wrap it).

### 3.5 Dashboard — retailer view
Add a **"Retailer view"**: durable trends (default weekly, high persistence) each shown with their inferred product categories and, where present, named products. This is the page to put in front of the lead — it reads as "trends → what to stock," not "rows in a table."

## 4. Module additions

```
src/
├── products.py          # Tier 1 inference + Tier 2 extraction (LLM), strict-JSON parsing
├── db.py                # + get_window_trends(), + upsert_trend_products()
├── pipeline.py          # + product step after metrics (wrapped/isolated)
└── ...
dashboard/app.py         # window toggle, filters, sort, persistence column, Retailer view
```

## 5. Build order (sequential, verify each)

1. **Persistence query** — `get_window_trends()` in `db.py`; add the `captured_date` index. Test against real data; print weekly trends ranked by `days_present`. (Most will have low counts — correct, collection is young.)
2. **Dashboard A** — window toggle + filters + sort + persistence column + sparkline. **This is the first demo. Verify it, show the lead if possible.**
3. **Schema** — `trend_products` table; apply to Neon.
4. **Tier 1 products** — `products.py` inference, batched; pipeline step for top ~50 trends; verify categories populate with sensible output.
5. **Dashboard Retailer view** — durable trends + product categories. **This is the boss demo combo (A + Tier 1).**
6. **Tier 2 products** — extract named products from top-20 hashtag videos; verify where caption text exists; confirm graceful empties elsewhere.
7. **Dashboard** — show named products under each trend in the Retailer view.
8. **README** — document persistence math, the cold-start denominator, the product tiers, and that Tier 3 (real Shop SKUs) and influencers are deferred pending a data-source decision.

## 6. Deferred (do NOT build here — they need a decision/budget)

- **Tier 3 — real TikTok Shop products with sales signal.** The highest-value retailer output (actual products tied to real demand), but the clean path needs a paid product-data source. This is the budget ask to bring to the lead *with the A + Tier 1 demo as leverage*: "you want real products — the clean source costs ~$X/mo."
- **Workstream C — influencer analysis per category.** Pull trending creators per category, summarize their content themes. A strong fast-follow once A and B land, but not in this phase.

## 7. Guardrails recap

- Workstream A needs **no new scraping** — build it first, it's the fast win.
- Persistence denominator uses **days collected so far** (cold-start honesty); show "X/Y days" in UI.
- Product inference is **limited to top/active trends** to control LLM cost; **batched**; strict-JSON with safe fallback.
- Tier 2 **degrades gracefully** when caption text is thin.
- Product step is **isolated** — its failure can't break ingestion/metrics.
- Reuse the Phase 1 categorizer and Phase 2 metrics engine; don't rebuild them.
- Same 10-category taxonomy.
- **No prediction model, no new platforms** — those are Phases 5 and 4.
