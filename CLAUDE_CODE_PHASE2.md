# TrendRadar — Phase 2 Build Spec (for Claude Code)

> **Read `PROJECT_PLAN.md` and `CLAUDE_CODE_PHASE1.md` first.** Phase 1 (collect + categorize + dashboard) is built and running. This file is the executable build spec for **Phase 2 — Measure**, and nothing beyond it.

---

## 0. Operating instructions for you, Claude Code

- **Run on Opus.** Confirm `/model opus` before continuing.
- **No LLM in this phase.** Phase 2 is pure computation over the `snapshots` table. There are zero Anthropic API calls here — don't add any.
- **Code style:** elementary and readable. The metric math must be plain, well-named, and obvious to a future reader. No clever vectorization tricks that hide the logic.
- **Scope boundary — hard line:** Phase 2 *describes* what the data already shows (how fast each trend is moving, where it sits on its lifecycle). It does **NOT predict** future explosions (Phase 3) and does **NOT recommend** anything to stores (Phase 4). If you start training a model or scoring "what will pop," stop — wrong phase.
- **Build now, comes alive later.** Real history is only a few days deep, so live output will mostly read `new` for a while. That's expected and correct. You will validate the math against **synthetic history** (§7), not against the thin real data.
- **Work the build order (§8) in sequence**, proving each step with real output before moving on.

## 1. What Phase 2 delivers

A daily computation step that, after each ingest, reads every trend's snapshot history and produces:
- a **smoothed count** (noise removed),
- a **velocity** (relative growth per day),
- an **acceleration** (is growth itself speeding up or slowing?),
- a **lifecycle stage** (`new` / `rising` / `cresting` / `declining` / `dormant`),

written to a new `metrics` table (one row per trend per day), and surfaced in the dashboard as stage badges, sparklines, and a "Rising now" view.

**Definition of done:** the metric functions are pure and unit-tested green against synthetic curves; the daily job computes and upserts metrics idempotently after ingestion; the dashboard shows each trend's stage + history sparkline and a working "Rising now" filter; trends with too little history correctly read `new` instead of showing garbage numbers.

## 2. Locked parameters

| Parameter | Locked value |
|---|---|
| Primary metric | **`video_count`** (creator-adoption proxy; present for both hashtags and sounds). `view_count` carried as secondary where present. |
| Velocity definition | **Relative growth rate, % per day** — not absolute change |
| Smoothing | **Trailing 3-snapshot moving average**, applied before differencing |
| Min history to compute | **4 snapshots.** Below that, stage = `new`, velocity/acceleration = null |
| Stages | **`new` / `rising` / `cresting` / `declining` / `dormant`** — transparent rule-based, no ML |
| Storage | New **`metrics`** table, one row per trend per `computed_date` |
| Compute timing | Daily, **after** ingestion (so today's snapshot is included) |

### Tunable thresholds (put these as named constants in one place at the top of `metrics.py`)

```python
MIN_SNAPSHOTS      = 4       # below this -> stage "new"
SMOOTH_WINDOW      = 3       # trailing moving-average window
VELOCITY_RISING    = 0.05    # >= +5%/day counts as rising
VELOCITY_DECLINING = -0.05   # <= -5%/day counts as declining
ACCEL_DECEL        = -0.02   # acceleration below this = meaningfully decelerating
DORMANT_DAYS       = 3       # no snapshot in this many days -> dormant
```

## 3. The math (specify exactly — no interpretation)

All of this operates on **one trend's snapshots**, ordered by `captured_date` ascending. Let the ordered values of `video_count` be `v[1..N]` on dates `d[1..N]`.

### 3.1 Smoothing
Trailing 3-snapshot mean: `s[i] = mean(v[i-2], v[i-1], v[i])` for `i >= 3`.
So smoothed values exist from `i = 3` onward. (With N=4 you get two smoothed points: `s[3]`, `s[4]`.)

### 3.2 Velocity (relative growth per day)
Use the two most recent smoothed points and normalize by the actual day gap so a missing day doesn't distort the rate:
```
Δdays   = max((d[N] - d[N-1]).days, 1)
velocity = ((s[N] - s[N-1]) / s[N-1]) / Δdays      # e.g. 0.20 = +20% per day
```
Guard: if `s[N-1] <= 0`, velocity = null (skip — avoids divide-by-zero on zero-base trends).
Requires N >= 4 (so `s[N-1]` exists).

### 3.3 Acceleration (change in velocity)
Compute a previous velocity from the prior pair of smoothed points, then difference:
```
velocity_prev = ((s[N-1] - s[N-2]) / s[N-2]) / Δdays_prev
acceleration  = velocity - velocity_prev
```
Requires `s[N-2]` to exist — **N >= 5**. For N = 4, acceleration = null. That's fine; the stage logic handles a null acceleration.

### 3.4 Stage — deterministic decision tree (evaluate top to bottom, first match wins)

```
if N < MIN_SNAPSHOTS:                               stage = "new"
elif (today - d[N]).days > DORMANT_DAYS:            stage = "dormant"   # fell off the list
elif velocity <= VELOCITY_DECLINING:                stage = "declining"
elif acceleration is not None
     and acceleration < ACCEL_DECEL
     and velocity > 0:                              stage = "cresting"  # still growing, but decelerating -> nearing peak
elif velocity >= VELOCITY_RISING:                   stage = "rising"
else:                                               stage = "cresting"  # flat top / plateau
```

Sanity checks this must satisfy (use them in tests):
- fast growth, accelerating — `rising`
- fast growth, sharply decelerating — `cresting`
- flat/plateau — `cresting`
- clear shrinkage — `declining`
- absent from list 3+ days — `dormant`
- under 4 snapshots — `new`

## 4. Database schema (append to `schema.sql`)

```sql
CREATE TABLE IF NOT EXISTS metrics (
    id              SERIAL PRIMARY KEY,
    trend_id        INTEGER NOT NULL REFERENCES trends(id),
    computed_date   DATE NOT NULL,
    smoothed_count  DOUBLE PRECISION,
    velocity        DOUBLE PRECISION,   -- relative growth/day; null if < 4 snapshots or zero base
    acceleration    DOUBLE PRECISION,   -- null if < 5 snapshots
    stage           TEXT NOT NULL,      -- new / rising / cresting / declining / dormant
    snapshot_count  INTEGER NOT NULL,   -- how many snapshots fed this computation
    UNIQUE (trend_id, computed_date)    -- one metrics row per trend per day; reruns idempotent
);

CREATE INDEX IF NOT EXISTS idx_metrics_date_stage ON metrics (computed_date, stage);
```

**Why store it instead of computing in the dashboard:** keeps the dashboard fast, and the daily `metrics` rows accumulate a **history of stage transitions** — which is exactly the labeled dataset Phase 3's predictor will train on. Don't skip persisting it.

## 5. Module design

**`src/metrics.py` — pure functions, no database.** This is the testable core.
- `smooth(values: list[float], window=SMOOTH_WINDOW) -> list[float]`
- `velocity(smoothed: list[float], day_gaps: list[int]) -> float | None`
- `acceleration(smoothed: list[float], day_gaps: list[int]) -> float | None`
- `classify_stage(snapshots: list[tuple[date, float]], today: date) -> dict` — returns `{smoothed_count, velocity, acceleration, stage, snapshot_count}`.
- All thresholds are the module-level constants from §2. These functions take plain data in and give plain data out — **zero DB access**, so they can be unit-tested in isolation.

**`src/compute_metrics.py` — the thin DB wrapper.**
- For each trend: load its snapshots ordered by date from Postgres, pass `(date, video_count)` pairs into `classify_stage`, and **upsert** the result into `metrics` for today's `computed_date` (`ON CONFLICT (trend_id, computed_date) DO UPDATE`).
- Runs **after** snapshot ingestion so today's data is included.

## 6. Pipeline & cron integration

- In `pipeline.py`, after snapshots for the day are written, call the compute step (or run `compute_metrics` as a second stage). Order is non-negotiable: **ingest — then compute.**
- In `.github/workflows/daily.yml`, this runs in the **same daily workflow**, right after ingestion. No new cron, no new secret.
- The whole daily run stays idempotent: re-running a day re-computes the same metrics rows rather than duplicating.

## 7. Synthetic validation (do this BEFORE trusting live output)

Real history is too thin to exercise the staging logic yet, so prove the math on fabricated curves.

**`tests/test_metrics.py`** — feed `classify_stage` hand-built snapshot sequences (~21 days each) and assert the stage:
- **clean riser** — exponential-ish growth — expect `rising`
- **peaker** — rises then flattens at the top — expect `cresting`
- **decliner** — was high, now dropping — expect `declining`
- **flat** — steady plateau — expect `cresting`
- **stale** — last snapshot 5 days ago — expect `dormant`
- **too short** — 3 snapshots — expect `new`
- **noisy riser** — upward trend with day-to-day jitter — still `rising` (this proves smoothing earns its place)

These are **pure-function tests — no database touched.** Keep synthetic data entirely out of the real `trends`/`snapshots`/`metrics` tables. If you want an end-to-end check against a DB, use a throwaway local/test database (`TEST_DATABASE_URL`), never the production one.

Run the suite; everything green before wiring into the live pipeline.

## 8. Build order (sequential, verify each)

1. **Schema** — add the `metrics` table to `schema.sql`, apply to Neon, confirm it exists.
2. **`metrics.py`** — pure functions + the threshold constants. The §3 math, exactly.
3. **`tests/test_metrics.py`** — the §7 synthetic cases. Run — all green. **Do not proceed until they pass.**
4. **`compute_metrics.py`** — load per-trend history, compute, upsert into `metrics`. Run manually against the real DB after a daily ingest; inspect the rows (most will be `new` — correct). Run it twice — confirm idempotency.
5. **Wire in** — call the compute step after ingestion in `pipeline.py`; confirm `daily.yml` runs it in-sequence.
6. **Dashboard** — add to `dashboard/app.py`: a color-coded **stage badge** per trend, a **sparkline** of its smoothed_count history, and a **"Rising now"** view (filter `stage = 'rising'`, sort by velocity desc). For `new` trends, show "history building — N days tracked" instead of empty velocity numbers.
7. **README** — document the metric definitions, the stage rules, and that metrics compute *after* ingest in the same daily job.

## 9. Guardrails recap

- Metric math lives in **pure, DB-free, unit-tested** functions.
- **No prediction, no scoring, no store logic** — that's Phase 3/4.
- Don't fabricate or forward-fill missing snapshots; normalize velocity by actual day gaps instead.
- Guard every division (`s[N-1] > 0`); null out metrics rather than emit garbage.
- All thresholds are named constants in one place — tunable without hunting through logic.
- Real vs synthetic data strictly separated.
- Daily compute runs **after** ingest and is idempotent per `computed_date`.
