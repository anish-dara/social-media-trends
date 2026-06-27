"""
Workstream B: turn trends into retailer-facing product suggestions.

Tier 1 (this file, for now) infers retail product categories from a trend's
name/category alone -- always works, no extra data needed. Tier 2 (top-video
caption mining) comes later and degrades gracefully when added. Reuses the
same Anthropic model as categorize.py (claude-sonnet-4-6) but keeps its own
client, matching categorize.py's pattern rather than reaching into it.
"""

import json
import os

from anthropic import Anthropic
from dotenv import load_dotenv

from src import db

load_dotenv()

MODEL = "claude-sonnet-4-6"

# Only infer products for trends with enough recent signal to be worth a
# retailer's attention -- see CLAUDE_CODE_PHASE3.md sec 3.2 ("don't infer
# products for dormant trends") and the cost-control note in the same section.
PRODUCT_WINDOW_DAYS = 7
PRODUCT_TOP_N = 50

TIER1_SYSTEM_PROMPT = (
    "You are a retail merchandising analyst. For each TikTok trend name given, "
    "infer 2-5 concrete retail product categories a physical or online store "
    "could stock in response to this trend, plus a one-sentence rationale. "
    'Respond with ONLY a JSON object mapping each input trend name to '
    '{"categories": [...], "rationale": "..."}. No prose, no markdown fences. '
    "If a trend has no sensible retail angle, return an empty categories list "
    "and a rationale saying so -- don't force a stretch."
)

# .strip() guards against a stray newline/space in the pasted key -- see the
# matching note in categorize.py.
_client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"].strip())


def infer_product_categories(trend_names):
    """
    trend_names: list of trend names (e.g. "#marathon2026").
    Returns dict {name: {"categories": [...], "rationale": "..."}}. Any name
    the model didn't return, or that fails to parse, gets an empty result
    rather than blocking the rest.
    """
    if not trend_names:
        return {}

    user_prompt = "Infer retail product categories for these TikTok trends:\n" + "\n".join(trend_names)
    response = _client.messages.create(
        model=MODEL,
        max_tokens=4000,
        system=TIER1_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    raw_text = response.content[0].text.strip()

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        parsed = {}

    results = {}
    for name in trend_names:
        entry = parsed.get(name)
        if isinstance(entry, dict) and isinstance(entry.get("categories"), list):
            results[name] = {"categories": entry["categories"], "rationale": entry.get("rationale", "")}
        else:
            results[name] = {"categories": [], "rationale": ""}
    return results


TIER2_SYSTEM_PROMPT = (
    "You extract concrete product and brand mentions from TikTok video captions. "
    "Given captions for videos under one hashtag, return ONLY a JSON object "
    '{"named_products": [{"product": "...", "brand": "...", "mentions": N}]}. '
    "Count how many captions mention each product. Use an empty string for brand "
    "if no brand is named. If the captions mention no concrete products, return "
    'an empty list. No prose, no markdown fences -- be conservative, do not '
    "invent products that aren't actually referenced."
)


def extract_named_products(captions):
    """
    captions: list of caption strings for one hashtag's top videos.
    Returns a list of {"product", "brand", "mentions"} dicts. Degrades
    gracefully: empty/whitespace input returns [] without an API call (don't
    pay for a guaranteed-empty extraction), and any parse failure returns [].
    """
    text = "\n".join(c.strip() for c in captions if c and c.strip())
    if not text:
        return []

    response = _client.messages.create(
        model=MODEL,
        max_tokens=1500,
        system=TIER2_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": "Captions:\n" + text}],
    )
    raw_text = response.content[0].text.strip()
    try:
        parsed = json.loads(raw_text)
        named = parsed.get("named_products", [])
        return named if isinstance(named, list) else []
    except (json.JSONDecodeError, AttributeError):
        return []


def compute_and_store_tier1(conn, generated_date, top_n=PRODUCT_TOP_N):
    """
    Selects the top `top_n` active trends (by persistence over
    PRODUCT_WINDOW_DAYS days, dormant ones excluded), infers Tier 1 product
    categories in one batched call, and upserts trend_products for today.
    Idempotent: ON CONFLICT (trend_id, generated_date) DO UPDATE.
    Returns the number of trends processed.
    """
    candidates = [
        r for r in db.get_window_trends(conn, PRODUCT_WINDOW_DAYS, sort_by="persistence")
        if r["stage"] != "dormant"
    ][:top_n]
    if not candidates:
        return 0

    by_name = {c["name"]: c for c in candidates}
    inferred = infer_product_categories(list(by_name.keys()))

    for name, candidate in by_name.items():
        db.upsert_trend_products(
            conn, candidate["id"], generated_date,
            product_categories=inferred.get(name, {"categories": [], "rationale": ""}),
        )
    return len(candidates)
