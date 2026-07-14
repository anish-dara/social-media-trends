"""
All EchoTik calls live here (mirrors tiktok_client.py / youtube.py / pinterest.py).

EchoTik (echotik.live) is a paid TikTok Shop data provider -- this is the
Tier 3 source: REAL products with REAL demand (units sold / GMV), which is the
one thing our heuristic Marketing ranking and the free social platforms can't
give. A product becomes a "trend" here: platform='tiktok_shop', type='product',
primary_metric = the sales signal, so it flows through the SAME velocity /
persistence / dashboard machinery as everything else -- a product's *sales
momentum* over time.

STATUS: scaffold, key-gated and dormant. It does nothing until ECHOTIK_API_KEY
is set, and any failure returns [] so it can never break the daily run. The
two things below marked  # CONFIRM  are the only unknowns -- EchoTik's exact
endpoint path and response field names -- because their API docs are behind a
login we don't have yet.

=== WHEN THE API KEY ARRIVES (3 steps) ===
1. Put the key in .env:            ECHOTIK_API_KEY=...
   and add it as a GitHub Actions secret so the cron gets it too:
       grep '^ECHOTIK_API_KEY=' .env | cut -d= -f2- | gh secret set ECHOTIK_API_KEY
   (then add `ECHOTIK_API_KEY: ${{ secrets.ECHOTIK_API_KEY }}` to daily.yml's env)
2. From the EchoTik dashboard, grab the trending-products endpoint's exact URL
   + auth style + one sample JSON response. Paste it to Claude (or fill the two
   # CONFIRM spots below): the ENDPOINT/AUTH block and the field mapping in
   _normalize().
3. Uncomment the ("tiktok_shop", _ingest_echotik) line in pipeline.SOURCES.
Then it flows end-to-end like any other platform; the Marketing tab can be
pointed at these real, sales-ranked products.
"""

import os

import httpx
from dotenv import load_dotenv

load_dotenv()

# CONFIRM: exact endpoint + params once we can see EchoTik's API docs/dashboard.
# (Docs say API-key auth, base at echotik.live, endpoints for product details /
# category trends / breakout patterns -- exact path TBD.)
BASE_URL = "https://api.echotik.live"          # CONFIRM
TRENDING_PRODUCTS_PATH = "/v1/products/trending"  # CONFIRM
REGION = "US"


def _api_key():
    key = os.environ.get("ECHOTIK_API_KEY", "").strip()
    return key or None


def _headers(key):
    # CONFIRM: EchoTik's auth style -- Bearer header is the common default, but
    # it may be `x-api-key: <key>` or an `api_key=` query param. Adjust to match.
    return {"accept": "application/json", "authorization": f"Bearer {key}"}


def _normalize(item):
    """
    Map one raw EchoTik product to the generic trend shape. CONFIRM the field
    names against a real response -- these keys are placeholders. `.get()`
    everywhere so a shape mismatch yields nulls, not a crash.
    """
    return {
        "name": item.get("title") or item.get("product_name"),          # CONFIRM
        "external_id": str(item.get("product_id") or item.get("id") or "") or None,  # CONFIRM
        "primary_metric": int(item.get("sold_count") or item.get("units_sold") or 0),  # CONFIRM (the demand signal)
        "secondary_metric": int(item.get("gmv") or item.get("revenue") or 0),          # CONFIRM
        "category_hint": item.get("category") or "",                     # CONFIRM
        "rank": item.get("rank"),
        "raw": item,
    }


def get_trending_products(limit=30, region=REGION):
    """
    Trending TikTok Shop products (by sales), normalized to the generic trend
    shape. Returns [] if there's no API key or on any error -- the daily run's
    per-source isolation logs and continues. Real endpoint/fields are confirmed
    on the first live call (see module docstring).
    """
    key = _api_key()
    if not key:
        return []  # dormant until the key is set

    try:
        resp = httpx.get(
            f"{BASE_URL}{TRENDING_PRODUCTS_PATH}",
            params={"region": region, "limit": limit},   # CONFIRM param names
            headers=_headers(key),
            timeout=20.0,
        )
        resp.raise_for_status()
        data = resp.json()
        # CONFIRM: where the product list lives in the response envelope.
        items = data.get("data") or data.get("products") or data.get("items") or []
    except (httpx.HTTPError, ValueError) as e:
        print(f"EchoTik fetch failed: {e}")
        return []

    results = []
    for i, item in enumerate(items[:limit], start=1):
        rec = _normalize(item)
        if not rec["name"]:
            continue
        rec["rank"] = rec["rank"] if isinstance(rec["rank"], int) else i
        results.append(rec)
    return results


if __name__ == "__main__":
    if not _api_key():
        print("ECHOTIK_API_KEY not set -- source is dormant (this is expected until "
              "the subscription/key is ready).")
    else:
        prods = get_trending_products(limit=10)
        print(f"Got {len(prods)} EchoTik products")
        for p in prods:
            print(f"  #{p['rank']:>2}  {p['name']:<40} sold={p['primary_metric']}")
