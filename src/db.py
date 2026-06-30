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


# Sort options for get_window_trends, whitelisted so sort_by can't be used to
# inject arbitrary SQL into ORDER BY.
_WINDOW_SORTS = {
    "persistence": "days_present DESC, velocity DESC NULLS LAST",
    "velocity": "velocity DESC NULLS LAST",
    "metric": "primary_metric DESC NULLS LAST",
    "rank": "rank ASC NULLS LAST",
}


def get_window_trends(conn, window_days, category=None, trend_type=None, stage=None, sort_by="persistence"):
    """
    Trends appearing at least once in the last `window_days` calendar days
    (the union over the window, not the intersection -- CLAUDE_CODE_PHASE3.md
    sec 2.1), each with `days_present` (distinct days seen in the window) and
    a cold-start-honest `effective_window` = min(window_days, total distinct
    days collected so far) so early trends don't get penalized for days
    before collection even started (sec 2.2). `platform` is hardcoded to
    'tiktok' for now -- there's only one platform until Phase 4 -- and
    `primary_metric` aliases `video_count` so the shape matches what a future
    multi-platform column would look like, without adding that column before
    it's needed.
    """
    sort_clause = _WINDOW_SORTS.get(sort_by, _WINDOW_SORTS["persistence"])

    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(DISTINCT captured_date) FROM snapshots")
        days_collected = cur.fetchone()[0] or 0
    effective_window = max(min(window_days, days_collected), 1)

    with conn.cursor() as cur:
        cur.execute(
            f"""
            WITH window_bounds AS (
                SELECT MAX(captured_date) AS latest_date FROM snapshots
            ),
            in_window AS (
                SELECT s.trend_id, COUNT(DISTINCT s.captured_date) AS days_present
                FROM snapshots s, window_bounds wb
                WHERE s.captured_date > wb.latest_date - %(window_days)s
                GROUP BY s.trend_id
            )
            SELECT
                t.id, t.name, t.type, t.category, 'tiktok' AS platform,
                iw.days_present,
                latest.rank, latest.video_count AS primary_metric,
                COALESCE(m.stage, 'new') AS stage, m.velocity, m.acceleration
            FROM in_window iw
            JOIN trends t ON t.id = iw.trend_id
            JOIN LATERAL (
                SELECT rank, video_count, captured_date
                FROM snapshots s2
                WHERE s2.trend_id = t.id
                ORDER BY captured_date DESC
                LIMIT 1
            ) latest ON true
            LEFT JOIN metrics m ON m.trend_id = t.id AND m.computed_date = latest.captured_date
            WHERE (%(category)s::text IS NULL OR t.category = %(category)s::text)
              AND (%(trend_type)s::text IS NULL OR t.type = %(trend_type)s::text)
              AND (%(stage)s::text IS NULL OR COALESCE(m.stage, 'new') = %(stage)s::text)
            ORDER BY {sort_clause}
            """,
            {
                "window_days": window_days,
                "category": category,
                "trend_type": trend_type,
                "stage": stage,
            },
        )
        columns = [desc[0] for desc in cur.description]
        rows = cur.fetchall()

    results = []
    for row in rows:
        record = dict(zip(columns, row))
        record["effective_window"] = effective_window
        record["persistence_ratio"] = record["days_present"] / effective_window
        results.append(record)
    return results


def upsert_trend_products(conn, trend_id, generated_date, product_categories=None, named_products=None):
    """
    One row per trend per day. ON CONFLICT (trend_id, generated_date) updates
    so reruns are idempotent. Tier 1 (product_categories) and Tier 2
    (named_products) can be written independently -- COALESCE keeps whichever
    tier already ran today if this call only supplies the other one.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO trend_products (trend_id, generated_date, product_categories, named_products)
            VALUES (%(trend_id)s, %(generated_date)s, %(product_categories)s, %(named_products)s)
            ON CONFLICT (trend_id, generated_date) DO UPDATE
                SET product_categories = COALESCE(EXCLUDED.product_categories, trend_products.product_categories),
                    named_products = COALESCE(EXCLUDED.named_products, trend_products.named_products)
            """,
            {
                "trend_id": trend_id,
                "generated_date": generated_date,
                "product_categories": _as_jsonb(product_categories),
                "named_products": _as_jsonb(named_products),
            },
        )
    conn.commit()


def get_top_influencers(conn, window_days, category=None):
    """
    Top creators across the window, aggregated from the `topCreators` block
    that GetHashtagList returns for every hashtag (already stored in
    snapshots.raw_json -- no new data collection). A creator's influence here
    is breadth: how many distinct trending hashtags they're a top creator on
    in the window. Returns dicts with handle, nickname, follower_count,
    hashtag_count, and the categories/hashtags they appear under, ranked by
    hashtag_count then followers. `category` filters to creators appearing on
    at least one hashtag in that category.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT MAX(captured_date) FROM snapshots")
        latest = cur.fetchone()[0]
        if latest is None:
            return []
        cur.execute(
            """
            SELECT t.name, t.category, s.raw_json
            FROM snapshots s
            JOIN trends t ON t.id = s.trend_id
            WHERE s.captured_date > %s - %s AND s.raw_json IS NOT NULL
            """,
            (latest, window_days),
        )
        rows = cur.fetchall()

        # hashtag name -> named product labels (latest Tier 2 run), for the
        # associative creator->products link. Not true attribution: TikTok
        # gives top creators per hashtag and top videos per hashtag, but no
        # creator<->video row (Creator Trends tab still "coming soon"). So this
        # reads as "creators in this space are surfacing these products."
        cur.execute(
            """
            SELECT t.name, tp.named_products
            FROM trend_products tp
            JOIN trends t ON t.id = tp.trend_id
            WHERE tp.generated_date = (SELECT MAX(generated_date) FROM trend_products)
              AND tp.named_products IS NOT NULL
            """
        )
        products_by_hashtag = {}
        for hashtag_name, named in cur.fetchall():
            items = (named or {}).get("named_products", []) if isinstance(named, dict) else []
            labels = []
            for p in items:
                product = (p.get("product") or "").strip()
                brand = (p.get("brand") or "").strip()
                if product:
                    labels.append(f"{product} ({brand})" if brand else product)
            if labels:
                products_by_hashtag[hashtag_name] = labels

    creators = {}
    for hashtag_name, cat, raw in rows:
        for c in (raw or {}).get("topCreators", []):
            uid = c.get("ttUID")
            handle = c.get("handleName")
            if not uid or not handle:
                continue
            entry = creators.setdefault(uid, {
                "handle": handle,
                "nickname": c.get("nickname"),
                "follower_count": 0,
                "hashtags": set(),
                "categories": set(),
            })
            entry["nickname"] = entry["nickname"] or c.get("nickname")
            try:
                entry["follower_count"] = max(entry["follower_count"], int(c.get("followedCnt") or 0))
            except (TypeError, ValueError):
                pass
            entry["hashtags"].add(hashtag_name)
            if cat:
                entry["categories"].add(cat)

    results = []
    for entry in creators.values():
        if category and category not in entry["categories"]:
            continue
        surfacing = []
        for h in entry["hashtags"]:
            surfacing.extend(products_by_hashtag.get(h, []))
        results.append({
            "handle": entry["handle"],
            "nickname": entry["nickname"],
            "follower_count": entry["follower_count"],
            "hashtag_count": len(entry["hashtags"]),
            "hashtags": sorted(entry["hashtags"]),
            "categories": sorted(entry["categories"]),
            "surfacing_products": sorted(set(surfacing)),
        })
    results.sort(key=lambda r: (r["hashtag_count"], r["follower_count"]), reverse=True)
    return results
