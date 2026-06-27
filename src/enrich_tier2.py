"""
Tier 2 named-product enrichment -- a MANUAL, locally-run step, deliberately
NOT part of the unattended daily cron.

Why manual: extracting concrete product/brand mentions needs video captions.
The only path to a hashtag's top-video URLs is the login-gated
GetHashtagDetail endpoint, and a logged-in TikTok Ads session's cookies
expire in ~3 days -- too short-lived for a hands-off daily job. So you run
this yourself when you have a fresh login:

  1. Log into ads.tiktok.com Creative Center in your browser.
  2. From a GetHashtagList/GetHashtagDetail request in DevTools, copy your
     Cookie header and your csrftoken cookie value.
  3. Put them in .env (gitignored -- cookies never go in chat or git):
        TIKTOK_COOKIE=<the full Cookie header string>
        TIKTOK_CSRF=<the csrftoken cookie value>
  4. Run:  .venv/bin/python3 -m src.enrich_tier2

It enriches the top hashtags (by persistence) with named products and writes
them to trend_products.named_products, which the dashboard Retailer view then
shows under each trend. Tier 1 (the automated product categories) is
unaffected and keeps running in the daily cron regardless.
"""

import datetime
import os
import time

from dotenv import load_dotenv

from src import db, products, tiktok_client

load_dotenv()

TOP_N_HASHTAGS = 20
VIDEOS_PER_HASHTAG = 8
WINDOW_DAYS = 7
OEMBED_SLEEP = 0.3


def run():
    cookie = os.environ.get("TIKTOK_COOKIE", "").strip()
    csrf = os.environ.get("TIKTOK_CSRF", "").strip()
    if not cookie or not csrf:
        print("TIKTOK_COOKIE and TIKTOK_CSRF must be set in .env (from a logged-in "
              "Creative Center session). See this module's docstring.")
        return

    today = datetime.datetime.now(datetime.timezone.utc).date()
    conn = db.connect()
    try:
        candidates = [
            r for r in db.get_window_trends(conn, WINDOW_DAYS, trend_type="hashtag", sort_by="persistence")
            if r["stage"] != "dormant"
        ][:TOP_N_HASHTAGS]

        # get_window_trends doesn't carry tiktok_id; look them up in one query.
        id_map = dict(conn.execute(
            "SELECT id, tiktok_id FROM trends WHERE id = ANY(%s)",
            ([c["id"] for c in candidates],),
        ).fetchall())

        enriched = 0
        for c in candidates:
            tiktok_id = id_map.get(c["id"])
            if not tiktok_id:
                continue
            try:
                video_urls = tiktok_client.get_hashtag_video_urls(tiktok_id, cookie, csrf)
            except Exception as e:
                # A stale/invalid session fails the same way for every hashtag,
                # so stop and report rather than hammer the endpoint 20 times.
                print(f"Stopping -- GetHashtagDetail failed on {c['name']}: {e}")
                break

            captions = []
            for url in video_urls[:VIDEOS_PER_HASHTAG]:
                caption = tiktok_client.get_video_caption(url)
                if caption:
                    captions.append(caption)
                time.sleep(OEMBED_SLEEP)

            named = products.extract_named_products(captions)
            db.upsert_trend_products(conn, c["id"], today,
                                     named_products={"named_products": named})
            print(f"{c['name']}: {len(captions)} captions -> {len(named)} named products")
            enriched += 1

        print(f"\nEnriched {enriched} hashtags with named products for {today}.")
    finally:
        conn.close()


if __name__ == "__main__":
    run()
