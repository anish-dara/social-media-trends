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
