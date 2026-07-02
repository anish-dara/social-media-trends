"""
All YouTube Data API v3 calls live in this one file (mirrors tiktok_client.py's
one-file rule). Official API, free tier: no scraping, no login, no bot
challenge -- a much cleaner source than TikTok.

Endpoint: GET https://www.googleapis.com/youtube/v3/videos
  chart=mostPopular, regionCode=US, part=snippet,statistics, maxResults=30
Cost: ~1 quota unit/call against a free 10,000/day budget -- effectively free.
Do NOT use search.list (100 units). Auth is a simple API key (YOUTUBE_API_KEY).

Trend unit = a trending video. It maps onto the same generic shape the rest of
the pipeline consumes: primary_metric = viewCount (what velocity/stage
difference over), secondary_metric = likeCount, category_hint = YouTube's own
category name (fed to the LLM categorizer).
"""

import os

import httpx
from dotenv import load_dotenv

load_dotenv()

API_BASE = "https://www.googleapis.com/youtube/v3"
REGION = "US"


def _key():
    return os.environ["YOUTUBE_API_KEY"].strip()


_category_cache = {}


def get_video_categories():
    """id -> human category name (e.g. '20' -> 'Gaming'), cached for the process."""
    if _category_cache:
        return _category_cache
    resp = httpx.get(f"{API_BASE}/videoCategories",
                     params={"part": "snippet", "regionCode": REGION, "key": _key()},
                     timeout=15.0)
    resp.raise_for_status()
    for item in resp.json().get("items", []):
        _category_cache[item["id"]] = item["snippet"]["title"]
    return _category_cache


def get_trending_videos(limit=30, region=REGION):
    """
    Top `limit` trending videos for the region, normalized to the generic
    trend shape: name (title), external_id (video id), primary_metric
    (viewCount), secondary_metric (likeCount), category_hint, rank, raw.
    Returns [] on quota exhaustion / API error so the daily run's per-source
    isolation can log and move on (never crash the whole pipeline).
    """
    try:
        categories = get_video_categories()
        resp = httpx.get(
            f"{API_BASE}/videos",
            params={"part": "snippet,statistics", "chart": "mostPopular",
                    "regionCode": region, "maxResults": limit, "key": _key()},
            timeout=20.0,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
    except (httpx.HTTPError, KeyError) as e:
        print(f"YouTube fetch failed: {e}")
        return []

    results = []
    for i, item in enumerate(items, start=1):
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        results.append({
            "name": snippet.get("title"),
            "external_id": item.get("id"),
            "primary_metric": int(stats.get("viewCount") or 0),
            "secondary_metric": int(stats.get("likeCount") or 0),
            "category_hint": categories.get(snippet.get("categoryId"), ""),
            "rank": i,
            "raw": item,
        })
    return results


if __name__ == "__main__":
    vids = get_trending_videos(limit=10)
    print(f"Got {len(vids)} trending videos\n")
    for v in vids:
        print(f"#{v['rank']:>2}  [{v['category_hint']:<22}] views={v['primary_metric']:>12,}  {v['name'][:50]}")
