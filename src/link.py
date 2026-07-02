"""
Cross-platform trend linker. Uses the LLM (same claude-sonnet-4-6 stack as
categorize.py -- no LangChain, no vector store; overkill at ~60 items/day and
weaker on cryptic hashtags where an embedding carries no signal but world
knowledge does) to group today's trends from different platforms that refer to
the same real-world topic. E.g. TikTok's "#gta6" and a YouTube "GTA 6 trailer"
video are the same underlying trend.

Only genuinely CROSS-platform groups are kept -- a topic present on TikTok AND
YouTube. That subset is the high-value "trending everywhere" signal (a stronger
corroboration than either platform alone). Match rate is expected to be low:
TikTok's breakout hashtags are often niche/foreign/cryptic with no YouTube
counterpart. That's fine -- sparse but high-signal.
"""

import json
import os

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = (
    "You link trending topics across social platforms. You are given today's "
    "trending items, each tagged with its platform. Group items that refer to "
    "the SAME real-world topic, event, product, person, or meme -- even when "
    "the wording differs (a hashtag vs a video title). Only output a group if "
    "it contains items from AT LEAST TWO different platforms. Be strict: if you "
    "are not confident two items are the same underlying topic, do not group "
    "them. Respond with ONLY a JSON array: "
    '[{"topic": "<short canonical label>", "members": ["<exact item name>", ...]}]. '
    "No prose, no markdown fences. If nothing genuinely spans platforms, return []."
)

_client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"].strip())


def link_trends(items):
    """
    items: list of {"name", "platform", "category"}.
    Returns list of {"topic", "members": [names]} for groups spanning >=2
    platforms. Members are matched back to the exact input names; anything the
    model invents or that doesn't span platforms is dropped. Empty on parse
    failure or no cross-platform overlap (degrade gracefully).
    """
    if len({it["platform"] for it in items}) < 2:
        return []  # need at least two platforms present to link across

    name_to_platform = {it["name"]: it["platform"] for it in items}
    listing = "\n".join(f"[{it['platform']}] {it['name']}" for it in items)

    response = _client.messages.create(
        model=MODEL,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": "Today's trending items:\n" + listing}],
    )
    raw = response.content[0].text.strip()
    try:
        groups = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(groups, list):
        return []

    linked = []
    for g in groups:
        members = [m for m in g.get("members", []) if m in name_to_platform]
        platforms = {name_to_platform[m] for m in members}
        if len(platforms) >= 2:  # re-verify cross-platform after matching back
            linked.append({"topic": g.get("topic", "").strip() or "untitled",
                           "members": members})
    return linked


def compute_and_store(conn, linked_date):
    """
    Load today's trends across platforms, link them, and upsert into
    trend_links. Returns the number of cross-platform topics stored. Idempotent:
    clears this date's links first so a rerun reflects the latest grouping.
    """
    from src import db as db_module  # local import to avoid cycles

    rows = conn.execute(
        """
        SELECT t.id, t.name, t.platform, t.category
        FROM snapshots s JOIN trends t ON t.id = s.trend_id
        WHERE s.captured_date = %s
        """,
        (linked_date,),
    ).fetchall()
    items = [{"trend_id": tid, "name": name, "platform": plat, "category": cat}
             for tid, name, plat, cat in rows]
    id_by_name = {it["name"]: it["trend_id"] for it in items}

    groups = link_trends(items)

    with conn.cursor() as cur:
        cur.execute("DELETE FROM trend_links WHERE linked_date = %s", (linked_date,))
        for g in groups:
            for name in g["members"]:
                trend_id = id_by_name.get(name)
                if trend_id is None:
                    continue
                cur.execute(
                    """
                    INSERT INTO trend_links (linked_date, topic, trend_id, platform)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (linked_date, topic, trend_id) DO NOTHING
                    """,
                    (linked_date, g["topic"], trend_id,
                     next(it["platform"] for it in items if it["trend_id"] == trend_id)),
                )
    conn.commit()
    return len(groups)


if __name__ == "__main__":
    from src import db
    conn = db.connect()
    latest = conn.execute("SELECT MAX(captured_date) FROM snapshots").fetchone()[0]
    n = compute_and_store(conn, latest)
    print(f"{n} cross-platform topics linked for {latest}\n")
    for topic, in conn.execute("SELECT DISTINCT topic FROM trend_links WHERE linked_date=%s", (latest,)).fetchall():
        members = conn.execute(
            "SELECT t.platform, t.name FROM trend_links l JOIN trends t ON t.id=l.trend_id WHERE l.linked_date=%s AND l.topic=%s",
            (latest, topic),
        ).fetchall()
        print(f"* {topic}")
        for plat, name in members:
            print(f"    [{plat}] {name[:55]}")
