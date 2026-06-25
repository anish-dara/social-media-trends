"""
Thin DB wrapper around metrics.py: loads each trend's snapshot history,
computes its current velocity/acceleration/stage, and upserts one metrics
row per trend per day. Runs after ingestion (see pipeline.py) so today's
snapshot is included in the computation.
"""

import datetime
from collections import Counter

from src import db as db_module
from src.metrics import classify_stage


def compute_and_store(conn, computed_date=None):
    """
    Computes and upserts a metrics row for every trend that has at least one
    snapshot. Idempotent: ON CONFLICT (trend_id, computed_date) DO UPDATE, so
    reruns for the same day update rather than duplicate.
    Returns a list of {trend_id, smoothed_count, velocity, acceleration,
    stage, snapshot_count} dicts, one per trend, for reporting.
    """
    # UTC, not local time -- see pipeline.py for why.
    computed_date = computed_date or datetime.datetime.now(datetime.timezone.utc).date()

    with conn.cursor() as cur:
        cur.execute("SELECT id FROM trends")
        trend_ids = [row[0] for row in cur.fetchall()]

    results = []
    for trend_id in trend_ids:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT captured_date, video_count FROM snapshots WHERE trend_id = %s",
                (trend_id,),
            )
            snapshots = [(row[0], row[1]) for row in cur.fetchall() if row[1] is not None]

        if not snapshots:
            continue

        result = classify_stage(snapshots, computed_date)

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO metrics (trend_id, computed_date, smoothed_count, velocity, acceleration, stage, snapshot_count)
                VALUES (%(trend_id)s, %(computed_date)s, %(smoothed_count)s, %(velocity)s, %(acceleration)s, %(stage)s, %(snapshot_count)s)
                ON CONFLICT (trend_id, computed_date) DO UPDATE
                    SET smoothed_count = EXCLUDED.smoothed_count,
                        velocity = EXCLUDED.velocity,
                        acceleration = EXCLUDED.acceleration,
                        stage = EXCLUDED.stage,
                        snapshot_count = EXCLUDED.snapshot_count
                """,
                {**result, "trend_id": trend_id, "computed_date": computed_date},
            )
        results.append({"trend_id": trend_id, **result})

    conn.commit()
    return results


if __name__ == "__main__":
    conn = db_module.connect()
    try:
        results = compute_and_store(conn)
    finally:
        conn.close()

    counts = Counter(r["stage"] for r in results)
    print(f"Computed metrics for {len(results)} trends")
    for stage, count in counts.most_common():
        print(f"  {stage:<10} {count}")
