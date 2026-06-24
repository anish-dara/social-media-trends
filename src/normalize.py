"""
Cleans tiktok_client's raw-ish dicts into records ready for db.py. No TikTok-
specific knowledge here -- just name standardization, count coercion, and
dedup within a single day's pull.
"""


def _clean_hashtag_name(raw_name):
    name = (raw_name or "").strip().lower()
    if name and not name.startswith("#"):
        name = f"#{name}"
    return name


def normalize_hashtags(raw_hashtags, country="US"):
    """
    raw_hashtags: list of dicts from tiktok_client.get_trending_hashtags()
    Returns clean records ready for db.py: type, name, tiktok_id, country,
    rank, video_count, view_count, trend_direction, raw. Rows missing a name
    or tiktok_id are dropped; duplicate names within the same pull keep the
    first (higher-ranked) occurrence.
    """
    seen_names = set()
    records = []
    for item in raw_hashtags:
        name = _clean_hashtag_name(item.get("name"))
        tiktok_id = item.get("tiktok_id")
        if name in ("", "#") or not tiktok_id:
            continue
        if name in seen_names:
            continue
        seen_names.add(name)

        try:
            video_count = int(item.get("video_count") or 0)
            view_count = int(item.get("view_count") or 0)
            rank = int(item["rank"]) if item.get("rank") is not None else None
        except (TypeError, ValueError):
            continue

        records.append({
            "type": "hashtag",
            "name": name,
            "tiktok_id": str(tiktok_id),
            "country": country,
            "rank": rank,
            "video_count": video_count,
            "view_count": view_count,
            "trend_direction": item.get("trend_direction"),
            "raw": item.get("raw"),
        })
    return records
