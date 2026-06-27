"""
All Anthropic API calls live here. One batched call classifies an entire
day's trend names rather than one call per name, using claude-sonnet-4-6
(see CLAUDE_CODE_PHASE1.md sec 0: this is the categorizer's model, distinct
from the Opus/Sonnet build agent writing this code).
"""

import json
import os

from anthropic import Anthropic
from dotenv import load_dotenv

from src import tiktok_client

load_dotenv()

MODEL = "claude-sonnet-4-6"

TAXONOMY = [
    "music", "fitness", "food", "beauty", "fashion",
    "lifestyle", "tech", "gaming", "finance", "other",
]

SYSTEM_PROMPT = (
    "You are a strict classifier for TikTok trend names. For each given name, "
    "assign exactly ONE category from this fixed list: " + ", ".join(TAXONOMY) + ". "
    "Respond with ONLY a JSON object mapping each input name to its category, "
    "no prose, no markdown code fences."
)

# .strip() guards against a stray newline/space sneaking into the key when
# it's pasted into .env -- that produces a confusing 401 "invalid x-api-key"
# even though the key itself is valid.
_client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"].strip())


def categorize_batch(names):
    """
    names: list of trend names (e.g. "#cleantok").
    Returns dict {name: category}. Any name missing from the model's reply,
    or assigned something outside TAXONOMY, falls back to "other".
    """
    if not names:
        return {}

    user_prompt = "Classify these TikTok trend names:\n" + "\n".join(names)
    response = _client.messages.create(
        model=MODEL,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    raw_text = response.content[0].text.strip()

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        parsed = {}

    return {name: parsed.get(name) if parsed.get(name) in TAXONOMY else "other" for name in names}


def categorize(name, trend_type="hashtag", context=""):
    """Single-name convenience wrapper around categorize_batch (module contract)."""
    return categorize_batch([name])[name]


def attach_demographics(records, top_n=20):
    """
    Mutates `records` in place: for the first `top_n` hashtags (by rank),
    fetches demographics via tiktok_client and sets record["demographics"].
    Currently always None -- see tiktok_client.get_hashtag_demographics for why.
    """
    hashtags = [r for r in records if r["type"] == "hashtag"]
    hashtags_sorted = sorted(hashtags, key=lambda r: r.get("rank") or 9999)
    for record in hashtags_sorted[:top_n]:
        record["demographics"] = tiktok_client.get_hashtag_demographics(record["tiktok_id"])
    return records


if __name__ == "__main__":
    raw = tiktok_client.get_trending_hashtags(limit=30)
    names = [item["name"] for item in raw]
    categories = categorize_batch(names)

    bad = [name for name, cat in categories.items() if cat not in TAXONOMY]
    print(f"Categorized {len(categories)} names, {len(bad)} outside taxonomy (should be 0)\n")
    for name, cat in categories.items():
        print(f"{cat:<10} {name}")
