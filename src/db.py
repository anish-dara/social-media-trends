"""
Neon Postgres access. Two operations only: upsert a trend's identity row, and
append a dated snapshot. Never update a snapshot's metrics after insert --
only add new dated rows (see PROJECT_PLAN.md sec 3 and 7).
"""

import os

import psycopg
from dotenv import load_dotenv
from psycopg.types.json import Jsonb

load_dotenv()


def connect():
    return psycopg.connect(os.environ["NEON_DATABASE_URL"])


def _as_jsonb(value):
    return Jsonb(value) if value is not None else None


def upsert_trend(conn, record):
    """
    record keys: type, name, tiktok_id, country, category, demographics, captured_date
    Inserts a new trend row on first sighting (first_seen_date = captured_date),
    or on repeat sightings fetches the existing id and refreshes category/
    tiktok_id/demographics. Returns the trend's id.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO trends (type, name, tiktok_id, category, country, first_seen_date, demographics)
            VALUES (%(type)s, %(name)s, %(tiktok_id)s, %(category)s, %(country)s, %(captured_date)s, %(demographics)s)
            ON CONFLICT (type, name, country) DO UPDATE
                SET category = EXCLUDED.category,
                    tiktok_id = EXCLUDED.tiktok_id,
                    demographics = EXCLUDED.demographics
            RETURNING id
            """,
            {**record, "demographics": _as_jsonb(record.get("demographics"))},
        )
        trend_id = cur.fetchone()[0]
    conn.commit()
    return trend_id


def insert_snapshot(conn, trend_id, snapshot):
    """
    snapshot keys: captured_date, rank, video_count, view_count, trend_direction, raw_json
    One row per trend per day; ON CONFLICT (trend_id, captured_date) updates so
    reruns for the same day are idempotent rather than duplicating.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO snapshots (trend_id, captured_date, rank, video_count, view_count, trend_direction, raw_json)
            VALUES (%(trend_id)s, %(captured_date)s, %(rank)s, %(video_count)s, %(view_count)s, %(trend_direction)s, %(raw_json)s)
            ON CONFLICT (trend_id, captured_date) DO UPDATE
                SET rank = EXCLUDED.rank,
                    video_count = EXCLUDED.video_count,
                    view_count = EXCLUDED.view_count,
                    trend_direction = EXCLUDED.trend_direction,
                    raw_json = EXCLUDED.raw_json
            """,
            {
                **snapshot,
                "trend_id": trend_id,
                "raw_json": _as_jsonb(snapshot.get("raw_json")),
            },
        )
    conn.commit()
