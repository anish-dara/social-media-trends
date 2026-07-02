"""
The daily run: ingest each platform's trends, then compute metrics, infer
products, and predict growth over everything. Idempotent for a given date
(UNIQUE constraints in schema.sql).

Sources are isolated: one platform failing (TikTok endpoint drift, YouTube
quota) logs an error and the run continues with whatever else succeeded --
"consistent daily collection" must not hinge on every source being up.

Not included: TikTok sounds (Creative Center's "Songs" tab has no data --
"coming soon"). TikTok demographics/Tier-2 captions are login-gated (manual
enrichment only). See README.
"""

import datetime
from collections import Counter

from src import (categorize, compute_metrics, db, link, normalize, predict, products,
                 tiktok_client, youtube)

COUNTRY = "US"
HASHTAG_LIMIT = 30
YOUTUBE_LIMIT = 30
DEMOGRAPHICS_TOP_N = 20


def _ingest_tiktok(conn, captured_date):
    """Fetch + categorize + store TikTok trending hashtags. Returns count."""
    raw = tiktok_client.get_trending_hashtags(limit=HASHTAG_LIMIT, country_code=COUNTRY)
    records = normalize.normalize_hashtags(raw, country=COUNTRY)

    categories = categorize.categorize_batch([r["name"] for r in records])
    for r in records:
        r["category"] = categories.get(r["name"], "other")
    categorize.attach_demographics(records, top_n=DEMOGRAPHICS_TOP_N)

    for r in records:
        trend_id = db.upsert_trend(conn, {
            "platform": "tiktok", "type": r["type"], "name": r["name"],
            "tiktok_id": r["tiktok_id"], "category": r["category"],
            "country": r["country"], "captured_date": captured_date,
            "demographics": r.get("demographics"),
        })
        db.insert_snapshot(conn, trend_id, {
            "captured_date": captured_date, "rank": r["rank"],
            "primary_metric": r["video_count"], "secondary_metric": r["view_count"],
            "video_count": r["video_count"], "view_count": r["view_count"],
            "trend_direction": r["trend_direction"], "raw_json": r["raw"],
        })
    return len(records)


def _ingest_youtube(conn, captured_date):
    """Fetch + categorize + store YouTube trending videos. Returns count."""
    records = youtube.get_trending_videos(limit=YOUTUBE_LIMIT, region=COUNTRY)
    if not records:
        return 0

    hints = {r["name"]: r["category_hint"] for r in records if r["name"]}
    categories = categorize.categorize_batch([r["name"] for r in records], hints=hints)

    for r in records:
        trend_id = db.upsert_trend(conn, {
            "platform": "youtube", "type": "video", "name": r["name"],
            "tiktok_id": r["external_id"],  # legacy column name; holds the external id
            "category": categories.get(r["name"], "other"),
            "country": COUNTRY, "captured_date": captured_date,
        })
        db.insert_snapshot(conn, trend_id, {
            "captured_date": captured_date, "rank": r["rank"],
            "primary_metric": r["primary_metric"], "secondary_metric": r["secondary_metric"],
            "raw_json": r["raw"],
        })
    return len(records)


SOURCES = [("tiktok", _ingest_tiktok), ("youtube", _ingest_youtube)]


def run(captured_date=None):
    # UTC, not local: the day boundary is the 06:00 UTC cron (PROJECT_PLAN sec 14).
    captured_date = captured_date or datetime.datetime.now(datetime.timezone.utc).date()

    conn = db.connect()
    ingested = {}
    try:
        for platform, ingest in SOURCES:
            try:
                ingested[platform] = ingest(conn, captured_date)
            except Exception as e:  # per-source isolation -- one down != run down
                print(f"{platform} ingestion FAILED (other sources unaffected): {e}")
                ingested[platform] = 0

        # Downstream stages run once over everything, after ingestion.
        metric_results = compute_metrics.compute_and_store(conn, computed_date=captured_date)

        try:
            products_processed = products.compute_and_store_tier1(conn, generated_date=captured_date)
        except Exception as e:
            print(f"Product inference FAILED (ingestion/metrics unaffected): {e}")
            products_processed = 0

        # YouTube named-product extraction (from stored title/description/tags,
        # no login needed). Isolated like the other product steps.
        try:
            yt_products = products.enrich_youtube_products(conn, generated_date=captured_date)
        except Exception as e:
            print(f"YouTube product extraction FAILED (rest unaffected): {e}")
            yt_products = 0

        try:
            predictions_stored = predict.compute_and_store(conn, predicted_date=captured_date)
        except Exception as e:
            print(f"Prediction FAILED (ingestion/metrics/products unaffected): {e}")
            predictions_stored = 0

        # Cross-platform linking, also isolated. Finds topics trending on >=2
        # platforms; often 0 when breakout hashtags don't overlap YouTube.
        try:
            topics_linked = link.compute_and_store(conn, linked_date=captured_date)
        except Exception as e:
            print(f"Cross-platform linking FAILED (rest unaffected): {e}")
            topics_linked = 0
    finally:
        conn.close()

    total = sum(ingested.values())
    print(f"Pipeline run for {captured_date}: {total} trends captured")
    for platform, n in ingested.items():
        print(f"  {platform:<10} {n}")
    stage_counts = Counter(m["stage"] for m in metric_results)
    print("Stages:", dict(stage_counts.most_common()))
    print(f"Product categories inferred for {products_processed} trends")
    print(f"YouTube videos with named products: {yt_products}")
    print(f"Growth predictions stored for {predictions_stored} trends")
    print(f"Cross-platform topics linked: {topics_linked}")
    return ingested


if __name__ == "__main__":
    run()
