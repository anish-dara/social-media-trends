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
    "trending items, each tagged with its platform. Group items ONLY when they "
    "refer to the SAME SPECIFIC, NAMEABLE real-world entity or event -- the "
    "same game, film, song, person, product, or news event -- even if the "
    "wording differs.\n\n"
    "STRICT RULES (false links are worse than missed ones):\n"
    "- Each member must independently and unambiguously be about that one "
    "specific entity/event. Read each title literally.\n"
    "- NEVER group items just because they share a broad category (both about "
    "gaming, both music, both trailers). Same theme is NOT the same topic.\n"
    "- The topic label must accurately describe EVERY member. If a candidate "
    "member isn't clearly about the labeled topic, leave it out.\n"
    "- A group needs members from at least TWO different platforms. A hashtag "
    "with no true counterpart on another platform is simply not a group.\n"
    "- When two items are genuinely the same specific entity/event, DO link "
    "them -- don't miss a real match. But never invent one to fill a group.\n\n"
    "Scan the whole list carefully for real matches before answering.\n\n"
    'Respond with ONLY a JSON array: '
    '[{"topic": "<specific entity/event>", "members": ["<exact item name>", ...]}]. '
    "No prose, no markdown fences."
)

_client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"].strip())


# A single LLM call loses recall on a long list (one real match hides among
# 150 items). So the largest platform is sliced into chunks and every chunk is
# compared against ALL items from the other platform(s), keeping each call
# small enough for reliable recall. A few calls instead of one; no new deps.
# Smaller chunks = better recall on sparse matches (at the cost of more calls);
# 20 empirically catches the sparse cross-platform matches reliably.
CHUNK_SIZE = 20


def _link_once(items):
    """One LLM linking call over `items`. Returns validated cross-platform groups."""
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

    out = []
    for g in groups:
        members = [m for m in g.get("members", []) if m in name_to_platform]
        if len({name_to_platform[m] for m in members}) >= 2:  # spans 2+ platforms
            out.append({"topic": g.get("topic", "").strip() or "untitled", "members": members})
    return out


VERIFY_SYSTEM_PROMPT = (
    "You are a strict fact-checker for a proposed trend link. Given a specific "
    "topic and a list of items, return ONLY the items that are genuinely and "
    "specifically about that exact topic -- not merely the same general theme "
    "(e.g. both gaming, both music). When unsure, exclude. Respond with ONLY a "
    'JSON array of the exact item strings that pass: ["<item>", ...]. No prose.'
)


def _verify_group(topic, members):
    """Second-opinion pass: keep only members the model confirms are genuinely
    about `topic`. Kills the 'force a salient entity into a plausible-looking
    match' failure mode. Returns the surviving members (may be fewer)."""
    listing = "\n".join(members)
    response = _client.messages.create(
        model=MODEL,
        max_tokens=800,
        system=VERIFY_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"Topic: {topic}\nItems:\n{listing}"}],
    )
    try:
        kept = json.loads(response.content[0].text.strip())
    except json.JSONDecodeError:
        return []
    return [m for m in members if m in set(kept)] if isinstance(kept, list) else []


def link_trends(items):
    """
    items: list of {"name", "platform", "category"}.
    Returns list of {"topic", "members": [names]} for groups spanning >=2
    platforms. Members map back to exact input names; invented or
    single-platform groups are dropped. Empty when nothing genuinely spans
    platforms (degrade gracefully). Chunks large inputs to protect recall,
    then verifies each proposed group to drop forced/hallucinated matches.
    """
    platforms = {it["platform"] for it in items}
    if len(platforms) < 2:
        return []  # need >=2 platforms to link across

    by_platform = {}
    for it in items:
        by_platform.setdefault(it["platform"], []).append(it)
    largest = max(by_platform, key=lambda p: len(by_platform[p]))
    anchor = [it for it in items if it["platform"] != largest]
    big = by_platform[largest]

    if len(items) <= CHUNK_SIZE + len(anchor):
        proposed = _link_once(items)
    else:
        # Slice the largest platform; each call sees all anchor items + one slice.
        merged = {}  # topic(lower) -> {"topic", "members" set}
        for start in range(0, len(big), CHUNK_SIZE):
            chunk = anchor + big[start:start + CHUNK_SIZE]
            for g in _link_once(chunk):
                key = g["topic"].lower()
                entry = merged.setdefault(key, {"topic": g["topic"], "members": set()})
                entry["members"].update(g["members"])
        proposed = [{"topic": e["topic"], "members": sorted(e["members"])}
                    for e in merged.values()]

    # Verify each proposed group, dropping forced/hallucinated members. Keep
    # only groups that still span >=2 platforms after verification.
    name_to_platform = {it["name"]: it["platform"] for it in items}
    verified = []
    for g in proposed:
        kept = _verify_group(g["topic"], g["members"])
        if len({name_to_platform[m] for m in kept if m in name_to_platform}) >= 2:
            verified.append({"topic": g["topic"], "members": kept})
    return verified


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
