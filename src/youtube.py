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

# Categories that support the mostPopular chart (confirmed live 2026-07-02).
# Pulling per-category as well as overall widens coverage a lot -- more surface
# for cross-platform linking and for product extraction -- still ~1 quota unit
# per call against a free 10k/day budget.
TRENDING_CATEGORY_IDS = ["1", "2", "10", "15", "17", "20", "22", "23", "24", "25", "26", "28"]
PER_CATEGORY_LIMIT = 10


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


def _fetch_most_popular(region, limit, category_id=None):
    """One videos.list mostPopular call (overall, or within a category). Returns
    raw items; [] on error so a single bad category can't sink the whole pull."""
    params = {"part": "snippet,statistics", "chart": "mostPopular",
              "regionCode": region, "maxResults": limit, "key": _key()}
    if category_id:
        params["videoCategoryId"] = category_id
    try:
        resp = httpx.get(f"{API_BASE}/videos", params=params, timeout=20.0)
        resp.raise_for_status()
        return resp.json().get("items", [])
    except httpx.HTTPError as e:
        print(f"YouTube fetch failed (category={category_id}): {e}")
        return []


def get_trending_videos(limit=30, region=REGION, include_categories=True):
    """
    Trending videos normalized to the generic trend shape: name (title),
    external_id (video id), primary_metric (viewCount), secondary_metric
    (likeCount), category_hint, description, tags, rank, raw.

    Pulls the overall mostPopular chart plus (by default) each category's
    mostPopular chart, deduped by video id -- much broader coverage than
    overall alone. Rank reflects first appearance (overall list first). Returns
    [] only if even the overall pull fails (per-source isolation upstream).
    """
    try:
        categories = get_video_categories()
    except httpx.HTTPError as e:
        print(f"YouTube categories fetch failed: {e}")
        categories = {}

    raw_items = _fetch_most_popular(region, limit)
    if include_categories:
        for cid in TRENDING_CATEGORY_IDS:
            raw_items.extend(_fetch_most_popular(region, PER_CATEGORY_LIMIT, category_id=cid))

    results, seen = [], set()
    for item in raw_items:
        vid = item.get("id")
        if not vid or vid in seen:
            continue
        seen.add(vid)
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        results.append({
            "name": snippet.get("title"),
            "external_id": vid,
            "primary_metric": int(stats.get("viewCount") or 0),
            "secondary_metric": int(stats.get("likeCount") or 0),
            "category_hint": categories.get(snippet.get("categoryId"), ""),
            "description": snippet.get("description", ""),
            "tags": snippet.get("tags", []),
            "rank": len(results) + 1,
            "raw": item,
        })
    return results


if __name__ == "__main__":
    vids = get_trending_videos(limit=10)
    print(f"Got {len(vids)} trending videos\n")
    for v in vids:
        print(f"#{v['rank']:>2}  [{v['category_hint']:<22}] views={v['primary_metric']:>12,}  {v['name'][:50]}")
