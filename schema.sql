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

-- Phase 3 Workstream A: persistence windows scan snapshots by date across
-- ALL trends, unlike the (trend_id, captured_date) index above which is
-- keyed per-trend. See CLAUDE_CODE_PHASE3.md sec 2.3.
CREATE INDEX IF NOT EXISTS idx_snapshots_captured_date ON snapshots (captured_date);

-- Phase 3 Workstream B: retailer product suggestions derived from trends.
-- See CLAUDE_CODE_PHASE3.md sec 3.1.
CREATE TABLE IF NOT EXISTS trend_products (
    id                 SERIAL PRIMARY KEY,
    trend_id           INTEGER NOT NULL REFERENCES trends(id),
    generated_date     DATE NOT NULL,
    product_categories JSONB,   -- Tier 1: inferred retail categories + rationale
    named_products     JSONB,   -- Tier 2: extracted product/brand mentions
    UNIQUE (trend_id, generated_date)
);

-- Phase 4 (Platform expansion): TikTok + YouTube behind a common shape.
-- Existing rows are TikTok; this migration preserves them. See PROJECT_PLAN.md.
ALTER TABLE trends ADD COLUMN IF NOT EXISTS platform TEXT NOT NULL DEFAULT 'tiktok';
ALTER TABLE trends DROP CONSTRAINT IF EXISTS trends_type_name_country_key;  -- old UNIQUE(type,name,country)
ALTER TABLE trends DROP CONSTRAINT IF EXISTS trends_platform_type_name_country_key;
ALTER TABLE trends ADD CONSTRAINT trends_platform_type_name_country_key
    UNIQUE (platform, type, name, country);
-- widen the type CHECK: YouTube trends are 'video', Pinterest are 'search'
ALTER TABLE trends DROP CONSTRAINT IF EXISTS trends_type_check;
ALTER TABLE trends ADD CONSTRAINT trends_type_check
    CHECK (type IN ('hashtag', 'sound', 'video', 'search'));

-- snapshots gain a generic metric the engines read regardless of platform
ALTER TABLE snapshots ADD COLUMN IF NOT EXISTS primary_metric   BIGINT;
ALTER TABLE snapshots ADD COLUMN IF NOT EXISTS secondary_metric BIGINT;
-- backfill existing TikTok rows so metrics/prediction history stays identical
UPDATE snapshots SET primary_metric = video_count WHERE primary_metric IS NULL;
UPDATE snapshots SET secondary_metric = view_count WHERE secondary_metric IS NULL;

-- Cross-platform linker: a topic that trends on >=2 platforms the same day.
-- Each row ties one trend into a named cross-platform topic cluster.
CREATE TABLE IF NOT EXISTS trend_links (
    id           SERIAL PRIMARY KEY,
    linked_date  DATE NOT NULL,
    topic        TEXT NOT NULL,   -- canonical label from the LLM linker
    trend_id     INTEGER NOT NULL REFERENCES trends(id),
    platform     TEXT NOT NULL,
    UNIQUE (linked_date, topic, trend_id)
);

CREATE INDEX IF NOT EXISTS idx_trend_links_date ON trend_links (linked_date);

-- Phase 5 (Predict): daily forward-growth probability per trend, from the
-- popularity-curve model in src/predict.py. Prototype-grade at current data
-- volume (see README) -- persisted so predictions can later be scored against
-- what actually happened, which is how the model earns trust over time.
CREATE TABLE IF NOT EXISTS predictions (
    id                  SERIAL PRIMARY KEY,
    trend_id            INTEGER NOT NULL REFERENCES trends(id),
    predicted_date      DATE NOT NULL,   -- the capture day the features came from
    growth_probability  DOUBLE PRECISION,-- P(video_count grows by next capture)
    model_version       TEXT,            -- so old predictions stay interpretable
    UNIQUE (trend_id, predicted_date)
);

-- Daily model quality, so AUC (Area Under the ROC Curve) can be tracked over
-- time -- the honest "does it improve as data accumulates?" signal.
CREATE TABLE IF NOT EXISTS model_metrics (
    id             SERIAL PRIMARY KEY,
    computed_date  DATE NOT NULL,
    model_version  TEXT NOT NULL,
    roc_auc        DOUBLE PRECISION,
    f1             DOUBLE PRECISION,
    n_examples     INTEGER,
    positives      INTEGER,
    UNIQUE (computed_date, model_version)
);
