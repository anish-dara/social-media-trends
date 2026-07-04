# TrendRadar — Demo Walkthrough

A tab-by-tab script for walking a supervisor through the live dashboard.
Everything here is backed by real data you can show. Honest caveats are
called out so nothing gets found out later.

**Dashboard:** http://localhost:8501
If it's not up: `cd social-media-trends && .venv/bin/streamlit run dashboard/app.py`

---

## Opening line (10 seconds)

> "Six weeks ago this was a database. Now it's an automated, multi-platform
> pipeline that watches trends *move*, tells a retailer what to stock and which
> specific products are surging, and is accumulating the history a predictor
> needs — running itself daily with no manual touch."

Every clause is demonstrated below.

---

## The one-sentence architecture

**Capture → Measure → Productize → Visualize.** Runs once a day on GitHub
Actions, writes to Postgres (Neon), surfaced in this dashboard. All TikTok
calls live in one file, all YouTube calls in one file, all LLM calls in two —
so any single source breaking is a one-file fix.

---

## Tab-by-tab

### 1. Trends
- **Show:** window toggle (daily/weekly/monthly), filters (platform, category,
  stage), the persistence column ("X/Y days"), sparklines.
- **Say:** "This is the union view — trends that consistently show up, not
  one-day flashes. Persistence is the durability signal. Filter to stage =
  rising, sort by persistence → durable *and* accelerating."
- **Backed claim:** 10 unbroken days of TikTok collection; velocity/stage
  computed daily.

### 2. Analytics
- **Show:** the lifecycle-stages-over-time line chart, category bars, top-mover
  trajectories.
- **Say:** "This is the 'watching trends move' part — how the pool's mix of
  rising/cresting/declining shifts day to day, which categories carry the
  momentum, and the actual trajectories of the top movers."
- **Backed claim:** real time-series over 10 days; latest day 7 rising /
  10 cresting / 22 dormant.

### 3. Marketing  ← strongest "not just a database" moment
- **Show:** the ranked specific products + bar chart; expand a row to show the
  driving trends.
- **Say:** "Concrete products pulled from trending videos, ranked by the
  momentum of the trends they appear in — Cyberpunk 2077, GamerSupps, Pandora,
  Roblox. Each lists the trends driving it, so the ranking is auditable."
- **⚠️ Say the caveat yourself:** "This is a marketing *lead* — products
  surging in culture — **not** verified sales data. Real demand numbers need a
  paid data source; I've scoped it (~$10–100/mo). That's the budget ask."

### 4. Retailer view
- **Show:** durable trends → inferred product categories + rationale.
- **Say:** "The store-facing version: for each durable trend, what a store
  could stock and why."

### 5. Influencers
- **Show:** top creators per category + the "surfacing" products column.
- **Say:** "Who's driving each category, and the products surfacing in the
  content they're top on — an associative signal, honestly labeled, since
  TikTok exposes no direct creator→video link."

### 6. Across platforms
- **Show:** cross-platform topics (may be empty today).
- **Say:** "When a topic trends on both TikTok and YouTube, it's corroborated —
  a stronger signal. It's sparse by design; most days there's no overlap. It
  caught a Venezuela earthquake trending on both."

### 7. Prediction (experimental)  ← handle with care
- **Show:** the metric tiles + the ROC-AUC-over-time line chart.
- **Say:** "The prediction *pipeline* is built and trains on real trajectory
  data — no fabricated data. We track quality over time: it's sitting near
  0.6 AUC as the dataset grew from 60 to 234 examples. Flat near chance is an
  honest finding — early-curve shape alone isn't enough to forecast adoption
  yet. Either more data or better features moves it; we'll see, and it's
  tracked transparently."
- **Do NOT say:** "we can predict which trends explode." (AUC 0.6 ≠ that.)

### 8. About
- Plain-language summary of the whole thing + honest limitations. Good place to
  land if they want the non-technical overview.

---

## Claims cheat-sheet

**Say flat-out (backed):**
- Runs unattended daily, 10 straight days (GitHub Actions history + 469 snapshots).
- Multi-platform architecture, live — TikTok (10 days history) + YouTube (came
  online Jul 2, now accumulating). *Don't say "10 days on both."*
- Measures momentum, not snapshots (velocity/stage, 15 passing unit tests).
- Turns trends into concrete products (319 category-days + 25 named products).

**Say with the caveat out loud:**
- Marketing products = leads, not verified demand (→ budget ask).
- Prediction = pipeline built + tracked honestly; not a working predictor yet.
- Cross-platform linking = works, but sparse.

**Do NOT claim:**
- Predicting trend explosions · products proven to sell · TikTok's full top-100
  (we use the no-login breakout signal) · any age/demographic insight (login-gated).

---

## If they push on things

- **"Why not generate data to train the model faster?"** → "Synthetic data is
  great for testing the math — we use it for that. But training a predictor on
  fabricated trends only learns our own assumptions; the accuracy would prove
  nothing about the real world and collapses under 'how did you validate it?'
  We chose a real 0.6 over a fake 0.95."
- **"Your AUC isn't improving."** → "Correct, and we're showing that honestly.
  The dataset is growing cleanly; model quality is flat near chance, which
  tells us curve-shape alone isn't sufficient. That's a real finding that
  points to what to try next — not a number we're hiding."
- **Red X in GitHub Actions?** → "A manual test run during development that
  surfaced a bug; fixed the same day. Scheduled runs are all green."

---

## The ask

More platforms, real product/sales data (Tier 3, ~$10–100/mo), and a few more
weeks of accumulation before the predictor is worth judging. The demo proves
the machine works; the budget buys the data that makes the products real.
