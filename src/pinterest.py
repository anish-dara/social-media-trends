"""
All Pinterest Trends calls live in this one file (mirrors tiktok_client.py /
youtube.py). Pinterest is a shopping-intent search engine, so its trending
searches map directly to retail categories -- "nails", "outfit ideas", "dinner
ideas", "wallpaper" -- which is exactly the retail-product signal this project
is about (unlike news/attention trends).

Endpoint confirmed live via DevTools (2026-07-10), no login / no API key:
  GET https://trends.pinterest.com/top_trends_filtered/
      ?lookbackWindow=2&endDate=<date>&rankingMethod=3&country=US
      &trendsPreset=2&numTermsToReturn=<n>

trendsPreset is the lever (rankingMethod has no effect): preset 2 returns
evergreen top shopping trends (beauty/fashion/food/home) that persist day to
day -- good for velocity/persistence -- whereas preset 3 is churny pop-culture
"breakout" noise. We use 2. Each term carries a searchCount (-> primary_metric)
plus Pinterest's own WoW/MoM/YoY growth (kept in raw for reference; the metrics
engine computes velocity from our own daily snapshots).

Pinterest clamps endDate to its latest available window (data lags ~5-6 days),
so we pass today's UTC date and take whatever it returns.
"""

import datetime

import httpx

TRENDS_URL = "https://trends.pinterest.com/top_trends_filtered/"
TRENDS_PRESET = 2          # evergreen top shopping trends (see module docstring)
COUNTRY = "US"

HEADERS = {
    "accept": "*/*",
    "referer": "https://trends.pinterest.com/",
    "x-new-site": "true",
    "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}


def get_trending_searches(limit=25, country=COUNTRY):
    """
    Trending Pinterest searches, normalized to the generic trend shape: name
    (the search term), external_id (None), primary_metric (searchCount),
    secondary_metric (None), category_hint ("" -- the LLM categorizes the term
    itself), rank, raw. Returns [] on any error so the daily run's per-source
    isolation can log and continue.
    """
    end_date = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    params = {
        "lookbackWindow": 2,
        "endDate": end_date,
        "rankingMethod": 3,
        "country": country,
        "trendsPreset": TRENDS_PRESET,
        "numTermsToReturn": limit,
    }
    try:
        resp = httpx.get(TRENDS_URL, params=params, headers=HEADERS, timeout=20.0)
        resp.raise_for_status()
        values = resp.json().get("values", [])
    except (httpx.HTTPError, ValueError) as e:
        print(f"Pinterest fetch failed: {e}")
        return []

    # `reverseRank` is highest-for-#1; convert to a normal 1-based rank.
    n = len(values)
    results = []
    for item in values:
        term = (item.get("term") or "").strip()
        if not term:
            continue
        reverse_rank = item.get("reverseRank")
        rank = (n - reverse_rank + 1) if isinstance(reverse_rank, int) else len(results) + 1
        results.append({
            "name": term,
            "external_id": None,
            "primary_metric": int(item.get("searchCount") or 0),
            "secondary_metric": None,
            "category_hint": "",
            "rank": rank,
            "raw": item,
        })
    results.sort(key=lambda r: r["rank"])
    return results


if __name__ == "__main__":
    trends = get_trending_searches(limit=25)
    print(f"Got {len(trends)} Pinterest trending searches\n")
    for t in trends:
        print(f"#{t['rank']:>2}  {t['name']:<30} searchCount={t['primary_metric']}")
