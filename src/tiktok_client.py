"""
All TikTok Creative Center calls live in this one file. If TikTok changes an
endpoint or response shape, this is the only file that needs fixing.

Endpoint confirmed live via DevTools on 2026-06-22/23 (see CLAUDE_CODE_PHASE1.md sec 3):
  POST https://ads.tiktok.com/CreativeOne/KnowledgeAPI/GetHashtagList
No login, no cookies, no signed tokens required for this call.

Important deviation from the original spec hypothesis: the full ranked top-100
hashtag table IS gated behind a logged-in TikTok Ads session (confirmed by
testing logged-out/incognito). Logging in for an unattended daily cron would
break the project's locked "no login required" decision and the session
cookies expire in days, not weeks. So instead, anonymous calls are looped
across TikTok's industryID filter (each industry bucket returns its own top 3
"breakout" hashtags) and the results are merged + deduped. See INDUSTRY_IDS.
"""

import time

import httpx

BASE_URL = "https://ads.tiktok.com/CreativeOne/KnowledgeAPI"

HEADERS = {
    "accept": "application/json, text/plain, */*",
    "content-type": "application/json",
    "origin": "https://ads.tiktok.com",
    "referer": "https://ads.tiktok.com/creative/creativeCenter/trends/hashtag",
    "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}

# Every industryID in the 1B-30B range confirmed by live probing (2026-06-23) to
# return a non-empty "breakout" bucket anonymously. IDs not in this list returned
# zero items when tested. Looping over these plus the default "all industries"
# call (industryID omitted) assembles a usable daily set with no login at all.
INDUSTRY_IDS = [
    10_000_000_000, 11_000_000_000, 12_000_000_000, 13_000_000_000, 14_000_000_000,
    15_000_000_000, 16_000_000_000, 17_000_000_000, 18_000_000_000, 19_000_000_000,
    20_000_000_000, 21_000_000_000, 22_000_000_000, 23_000_000_000, 24_000_000_000,
    25_000_000_000, 27_000_000_000, 28_000_000_000, 29_000_000_000,
]

SLEEP_BETWEEN_CALLS = 1.5
MAX_RETRIES = 3

_session = httpx.Client(headers=HEADERS, timeout=15.0)


def _post_with_retry(path, json_body):
    """POST with exponential backoff on 4xx/5xx, up to MAX_RETRIES tries."""
    delay = 2.0
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = _session.post(f"{BASE_URL}/{path}", json=json_body)
            if resp.status_code < 400:
                return resp.json()
        except httpx.HTTPError:
            if attempt == MAX_RETRIES:
                raise
        if attempt == MAX_RETRIES:
            resp.raise_for_status()
        time.sleep(delay)
        delay *= 2
    return None


def _fetch_hashtag_bucket(industry_id, time_range, country_code):
    body = {"timeRange": time_range, "countryCode": country_code, "page": 1, "limit": 20}
    if industry_id is not None:
        body["industryID"] = industry_id
    data = _post_with_retry("GetHashtagList", body)
    if not data or "items" not in data:
        return []  # e.g. {"code": "InvalidRequest", ...} for a bad industryID
    return data["items"]


def get_trending_hashtags(limit=30, time_range=7, country_code="US"):
    """
    Returns up to `limit` trending hashtags, deduped and merged from TikTok
    Creative Center's per-industry breakout buckets.

    Each dict: name, tiktok_id, rank, video_count, view_count, trend_direction, raw.
    `trend_direction` is always None here -- the real API has no rising/falling/
    stable label (that was the spec's hypothesis, not reality). `raw` keeps the
    full item including TikTok's own 7-point popularityCurve, in case a later
    phase wants to derive direction from it.
    """
    seen = {}
    for industry_id in [None] + INDUSTRY_IDS:
        for item in _fetch_hashtag_bucket(industry_id, time_range, country_code):
            tiktok_id = item.get("hashtagID")
            if tiktok_id and tiktok_id not in seen:
                seen[tiktok_id] = item
        time.sleep(SLEEP_BETWEEN_CALLS)

    # No TikTok-provided global rank exists for this assembled set, so rank by
    # view count to keep "rank" meaningful (position in today's captured set).
    ordered = sorted(seen.values(), key=lambda it: int(it.get("vv") or 0), reverse=True)
    ordered = ordered[:limit]

    results = []
    for i, item in enumerate(ordered, start=1):
        results.append({
            "name": item.get("hashtagName"),
            "tiktok_id": item.get("hashtagID"),
            "rank": i,
            "video_count": int(item.get("publishCnt") or 0),
            "view_count": int(item.get("vv") or 0),
            "trend_direction": None,
            "raw": item,
        })
    return results


def get_hashtag_demographics(tiktok_id):
    """
    Per-hashtag audience age/country breakdown.

    Always returns None. The live endpoint for this (GetHashtagDetail) is
    confirmed gated behind a logged-in TikTok Ads session (returns
    BaseResp.StatusCode 38001001 "InvalidLogin" with no cookies), and this
    project deliberately stays anonymous -- see the module docstring. db.py /
    normalize.py treat None as "unknown", matching the spec's own rule: mark
    unknown rather than fabricate demographic data.
    """
    return None


# --- Tier 2 caption path (manual, login-fed -- NOT part of the daily cron) ---
#
# GetHashtagDetail is login-gated and its videoList carries video URLs but NO
# caption text (confirmed by a logged-in DevTools capture, 2026-06-27). So
# captions come in two hops: (1) this call, with the caller's own session
# cookies, returns the top videos' URLs for a hashtag; (2) get_video_caption()
# turns each URL into its caption via TikTok's public, no-login oEmbed
# endpoint. Cookies expire in ~3 days, so step 1 can't live in the unattended
# cron -- this is a manual local enrichment step (see src/enrich_tier2.py).

OEMBED_URL = "https://www.tiktok.com/oembed"


def get_hashtag_video_urls(hashtag_id, cookie_header, csrf_token, time_range=90):
    """
    Canonical web URLs for a hashtag's top videos, via the login-gated
    GetHashtagDetail. `cookie_header` is the raw Cookie string from a logged-in
    Creative Center session; `csrf_token` is the csrftoken cookie's value (sent
    as X-CSRFToken).

    Each videoList item carries `itemID` (the real video ID) plus a `videoURL`
    object whose `.default` is a raw CDN *media* URL -- NOT a page URL, and
    oEmbed can't resolve those. So we build the canonical
    https://www.tiktok.com/@x/video/<itemID> form from itemID instead. The
    username segment is irrelevant: oEmbed resolves purely by the video ID
    (confirmed -- @placeholder / @tiktok / @_ all return the same caption).

    Returns a list of URL strings (empty if this hashtag has no detail videos --
    some don't). Raises on a non-200 or an InvalidLogin BaseResp so the caller
    can report a stale/absent session clearly rather than silently produce
    nothing.
    """
    headers = {
        **HEADERS,
        "agw-js-conv": "str",
        "x-csrftoken": csrf_token,
        "cookie": cookie_header,
    }
    resp = httpx.post(
        f"{BASE_URL}/GetHashtagDetail",
        headers=headers,
        json={"hashtagID": str(hashtag_id), "timeRange": time_range, "countryCode": "US"},
        timeout=15.0,
    )
    resp.raise_for_status()
    data = resp.json()
    base = data.get("BaseResp", {})
    if base.get("StatusCode") not in (0, None):
        raise RuntimeError(f"GetHashtagDetail returned {base.get('StatusMessage')!r} "
                           f"(login likely expired or missing)")
    return [
        f"https://www.tiktok.com/@x/video/{v['itemID']}"
        for v in data.get("videoList", []) if v.get("itemID")
    ]


def get_video_caption(video_url):
    """
    The caption for one TikTok video URL, via the public oEmbed endpoint
    (no login, no key). Returns the caption string, or None if the video is
    private/removed/unavailable. Confirmed live 2026-06-27: oEmbed's `title`
    field is the full caption including hashtags.
    """
    try:
        resp = httpx.get(OEMBED_URL, params={"url": video_url}, timeout=15.0)
        if resp.status_code != 200:
            return None
        return resp.json().get("title")
    except (httpx.HTTPError, ValueError):
        return None


if __name__ == "__main__":
    hashtags = get_trending_hashtags(limit=30)
    print(f"Got {len(hashtags)} hashtags\n")
    for h in hashtags:
        print(f"#{h['rank']:>2}  {h['name']:<30} videos={h['video_count']:<10} views={h['view_count']}")
