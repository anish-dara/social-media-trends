"""
The daily run: fetch hashtags -> normalize -> categorize (batch) -> attach
demographics for top-20 hashtags -> upsert trends -> insert today's snapshots
-> print a summary. Idempotent for a given date: reruns update existing rows
rather than duplicating, thanks to the UNIQUE constraints in schema.sql.

Sounds are not included. TikTok Creative Center's "Songs" trends tab has no
data right now -- it shows "coming soon" in the product itself (confirmed
live on 2026-06-23). Revisit tiktok_client.get_trending_sounds (not yet
written) once TikTok ships that data. See PROJECT_PLAN.md.
"""

import datetime
from collections import Counter

from src import categorize, compute_metrics, db, normalize, predict, products, tiktok_client

COUNTRY = "US"
HASHTAG_LIMIT = 30
DEMOGRAPHICS_TOP_N = 20


def run(captured_date=None):
    # UTC, not local time: the project's day boundary is the 06:00 UTC cron
    # (PROJECT_PLAN.md sec 14), so local-time date.today() would disagree
    # with the GitHub Actions runner (which is UTC) by several hours
    # depending on where this is run from.
    captured_date = captured_date or datetime.datetime.now(datetime.timezone.utc).date()

    raw_hashtags = tiktok_client.get_trending_hashtags(limit=HASHTAG_LIMIT, country_code=COUNTRY)
    records = normalize.normalize_hashtags(raw_hashtags, country=COUNTRY)

    categories = categorize.categorize_batch([r["name"] for r in records])
    for r in records:
        r["category"] = categories.get(r["name"], "other")

    categorize.attach_demographics(records, top_n=DEMOGRAPHICS_TOP_N)

    conn = db.connect()
    try:
        for r in records:
            trend_id = db.upsert_trend(conn, {
                "type": r["type"],
                "name": r["name"],
                "tiktok_id": r["tiktok_id"],
                "category": r["category"],
                "country": r["country"],
                "captured_date": captured_date,
                "demographics": r.get("demographics"),
            })
            db.insert_snapshot(conn, trend_id, {
                "captured_date": captured_date,
                "rank": r["rank"],
                "video_count": r["video_count"],
                "view_count": r["view_count"],
                "trend_direction": r["trend_direction"],
                "raw_json": r["raw"],
            })

        # Metrics compute runs after ingestion so today's snapshot is
        # included (see CLAUDE_CODE_PHASE2.md sec 6 -- order is non-negotiable).
        metric_results = compute_metrics.compute_and_store(conn, computed_date=captured_date)

        # Product inference runs last and is isolated: a failure here (LLM
        # hiccup, bad JSON) must never break ingestion/metrics, which already
        # succeeded by this point (CLAUDE_CODE_PHASE3.md sec 3.4).
        try:
            products_processed = products.compute_and_store_tier1(conn, generated_date=captured_date)
        except Exception as e:
            print(f"Product inference FAILED (ingestion/metrics unaffected): {e}")
            products_processed = 0

        # Forward-growth prediction, also isolated. Trains on all real history
        # so far and scores today's trends. Prototype-grade until the dataset
        # grows (see README) -- and a failure must not break the daily run.
        try:
            predictions_stored = predict.compute_and_store(conn, predicted_date=captured_date)
        except Exception as e:
            print(f"Prediction FAILED (ingestion/metrics/products unaffected): {e}")
            predictions_stored = 0
    finally:
        conn.close()

    counts = Counter(r["category"] for r in records)
    print(f"Pipeline run for {captured_date}: {len(records)} trends captured")
    for category, count in counts.most_common():
        print(f"  {category:<10} {count}")

    stage_counts = Counter(m["stage"] for m in metric_results)
    print("Stages:")
    for stage, count in stage_counts.most_common():
        print(f"  {stage:<10} {count}")

    print(f"Product categories inferred for {products_processed} trends")
    print(f"Growth predictions stored for {predictions_stored} trends")

    return records


if __name__ == "__main__":
    run()
