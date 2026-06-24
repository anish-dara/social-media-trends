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
