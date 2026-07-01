"""
Read-only Streamlit dashboard: today's top trends by category, age
demographics where available, lifecycle stage badges with a smoothed-count
sparkline, a "Rising now" view, and an "About" tab explaining the project.
No writes happen here -- pipeline.py and compute_metrics.py own all inserts.
"""

import sys
from pathlib import Path

# `streamlit run dashboard/app.py` puts dashboard/ on sys.path, not the repo
# root, so `from src import ...` fails unless we add the root ourselves.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import datetime

import streamlit as st

from src import db
from src.categorize import TAXONOMY

STAGE_EMOJI = {"new": "⚪", "rising": "\U0001f7e2", "cresting": "\U0001f7e1",
               "declining": "\U0001f534", "dormant": "⚫"}
STAGES = ["new", "rising", "cresting", "declining", "dormant"]
SORT_OPTIONS = {"Persistence": "persistence", "Velocity": "velocity",
                 "Metric": "metric", "Rank": "rank"}


def _display(records):
    """Strip internal sort/filter-only fields before handing rows to st.dataframe."""
    return [{k: v for k, v in r.items() if not k.startswith("_")} for r in records]


st.set_page_config(page_title="TrendRadar", layout="wide")
st.title("TrendRadar")

trends_tab, retailer_tab, influencers_tab, predict_tab, about_tab = st.tabs(
    ["Trends", "Retailer view", "Influencers", "Prediction (experimental)", "About"]
)

with trends_tab:
    conn = db.connect()
    latest_date = conn.execute("SELECT MAX(captured_date) FROM snapshots").fetchone()[0]

    if latest_date is None:
        st.info("No snapshots yet -- run `python -m src.pipeline` to capture today's trends.")
    else:
        st.caption(f"Latest capture: {latest_date} · platform: tiktok (more platforms planned for Phase 4)")

        filter_cols = st.columns(5)
        with filter_cols[0]:
            window_choice = st.selectbox("Window", ["Daily", "Weekly", "Monthly", "Past N days", "Date range"])
        if window_choice == "Daily":
            window_days = 1
        elif window_choice == "Weekly":
            window_days = 7
        elif window_choice == "Monthly":
            window_days = 30
        elif window_choice == "Past N days":
            with filter_cols[0]:
                window_days = st.number_input("N", min_value=1, max_value=365, value=14)
        else:  # Date range
            with filter_cols[0]:
                start_date = st.date_input("From", value=latest_date - datetime.timedelta(days=13),
                                            max_value=latest_date)
            window_days = max((latest_date - start_date).days + 1, 1)

        with filter_cols[1]:
            category_choice = st.selectbox("Category", ["All"] + TAXONOMY)
        with filter_cols[2]:
            type_choice = st.selectbox("Type", ["All", "hashtag", "sound"])
        with filter_cols[3]:
            stage_choice = st.selectbox("Stage", ["All"] + STAGES)
        with filter_cols[4]:
            sort_choice = st.selectbox("Sort by", list(SORT_OPTIONS.keys()))

        results = db.get_window_trends(
            conn, window_days,
            category=None if category_choice == "All" else category_choice,
            trend_type=None if type_choice == "All" else type_choice,
            stage=None if stage_choice == "All" else stage_choice,
            sort_by=SORT_OPTIONS[sort_choice],
        )

        effective_window = results[0]["effective_window"] if results else window_days
        st.caption(
            f"{len(results)} trends · window: {effective_window} day"
            f"{'s' if effective_window != 1 else ''}"
            + (f" (asked for {window_days}, but only {effective_window} days collected so far)"
               if effective_window < window_days else "")
        )
        st.caption(
            "Tip: Stage = rising + Sort by = Persistence surfaces trends that are "
            "both durable (show up consistently) and currently accelerating."
        )

        trend_ids = [r["id"] for r in results]
        history_rows = conn.execute(
            "SELECT trend_id, smoothed_count FROM metrics WHERE trend_id = ANY(%s) ORDER BY computed_date",
            (trend_ids,),
        ).fetchall()
        history_by_trend = {}
        for trend_id, smoothed in history_rows:
            if smoothed is not None:
                history_by_trend.setdefault(trend_id, []).append(smoothed)

        records = []
        for r in results:
            if r["velocity"] is None:
                velocity_display = "history building"
            else:
                velocity_display = f"{r['velocity']:+.1%}/day"
            records.append({
                "name": r["name"],
                "category": r["category"] or "other",
                "stage": f"{STAGE_EMOJI.get(r['stage'], '')} {r['stage']}",
                "velocity": velocity_display,
                "persistence": f"{r['days_present']}/{r['effective_window']} days",
                "rank": r["rank"],
                "primary_metric": r["primary_metric"],
                "smoothed_history": history_by_trend.get(r["id"], []),
            })

        st.dataframe(
            _display(records),
            width="stretch",
            hide_index=True,
            column_config={"smoothed_history": st.column_config.LineChartColumn("Smoothed history")},
        )

        with st.expander("Age demographics"):
            with_demographics = conn.execute(
                "SELECT name, demographics FROM trends WHERE demographics IS NOT NULL"
            ).fetchall()
            if with_demographics:
                for name, demographics in with_demographics:
                    st.write(name, demographics)
            else:
                st.write(
                    "No age demographic data available. Creative Center's per-hashtag "
                    "demographics endpoint requires a logged-in TikTok Ads session, which "
                    "this project deliberately avoids -- see PROJECT_PLAN.md."
                )

with retailer_tab:
    st.subheader("Durable trends → what a store could stock")
    st.caption(
        "Trends ranked by how consistently they appear (durable, not one-day "
        "flashes), each mapped to retail product categories a store could act "
        "on. Product suggestions are generated daily for the top trends."
    )

    conn_r = db.connect()
    if conn_r.execute("SELECT MAX(captured_date) FROM snapshots").fetchone()[0] is None:
        st.info("No data yet -- run `python -m src.pipeline`.")
    else:
        r_cols = st.columns(3)
        with r_cols[0]:
            r_window = st.selectbox("Window", ["Weekly", "Monthly", "Daily"], key="retailer_window")
            r_window_days = {"Daily": 1, "Weekly": 7, "Monthly": 30}[r_window]
        with r_cols[1]:
            r_category = st.selectbox("Category", ["All"] + TAXONOMY, key="retailer_category")
        with r_cols[2]:
            durable_rising = st.checkbox("Durable + rising only", value=False)

        r_results = db.get_window_trends(
            conn_r, r_window_days,
            category=None if r_category == "All" else r_category,
            stage="rising" if durable_rising else None,
            sort_by="persistence",
        )

        # Pull the latest product_categories (Tier 1, written daily by the
        # cron) and the latest named_products (Tier 2, written by the manual
        # enrich step) PER TREND, independently. The two tiers commonly land
        # on different generated_dates, so keying both off one MAX(date) would
        # drop whichever tier ran on the other day. DISTINCT ON picks each
        # field's most recent non-null row per trend.
        products_by_trend = {}
        if r_results:
            trend_ids = [r["id"] for r in r_results]
            cat_rows = conn_r.execute(
                """
                SELECT DISTINCT ON (trend_id) trend_id, product_categories
                FROM trend_products
                WHERE trend_id = ANY(%s) AND product_categories IS NOT NULL
                ORDER BY trend_id, generated_date DESC
                """,
                (trend_ids,),
            ).fetchall()
            named_rows = conn_r.execute(
                """
                SELECT DISTINCT ON (trend_id) trend_id, named_products
                FROM trend_products
                WHERE trend_id = ANY(%s) AND named_products IS NOT NULL
                ORDER BY trend_id, generated_date DESC
                """,
                (trend_ids,),
            ).fetchall()
            cats = dict(cat_rows)
            nameds = dict(named_rows)
            for tid in set(cats) | set(nameds):
                products_by_trend[tid] = (cats.get(tid), nameds.get(tid))

        shown = 0
        for r in r_results:
            product_data = products_by_trend.get(r["id"])
            categories = (product_data[0] or {}).get("categories", []) if product_data else []
            named = (product_data[1] or {}).get("named_products", []) if product_data else []
            if not categories and not named:
                continue  # no retail angle inferred -- don't show an empty card
            shown += 1

            stage_label = f"{STAGE_EMOJI.get(r['stage'], '')} {r['stage']}"
            persistence = f"{r['days_present']}/{r['effective_window']} days"
            st.markdown(f"### {r['name']}  ·  {r['category'] or 'other'}")
            st.caption(f"{stage_label} · appeared {persistence}")
            if categories:
                st.markdown("**Stock:** " + " · ".join(categories))
            rationale = (product_data[0] or {}).get("rationale", "") if product_data else ""
            if rationale:
                st.caption(rationale)
            if named:
                named_str = ", ".join(
                    f"{n.get('product', '')}" + (f" ({n['brand']})" if n.get("brand") else "")
                    for n in named
                )
                st.markdown("**People are using:** " + named_str)
            st.divider()

        if shown == 0:
            st.info(
                "No product suggestions for this view yet. They're generated for "
                "the top trends each daily run -- widen the window or run "
                "`python -m src.pipeline`."
            )

with influencers_tab:
    st.subheader("Top creators driving trending hashtags")
    st.caption(
        "Aggregated from the top creators TikTok lists for each trending "
        "hashtag (no extra data collection). Ranked by breadth -- how many "
        "distinct trending hashtags a creator appears on -- then followers. "
        "**Surfacing** shows products appearing in the videos of the hashtags "
        "a creator is top on: an associative signal -- \"creators in this "
        "space are surfacing these products\" -- not direct attribution. "
        "TikTok exposes no creator-to-video link (its Creator Trends tab is "
        "still \"coming soon\"), so true per-creator attribution isn't "
        "possible from this source yet."
    )

    conn_i = db.connect()
    if conn_i.execute("SELECT MAX(captured_date) FROM snapshots").fetchone()[0] is None:
        st.info("No data yet -- run `python -m src.pipeline`.")
    else:
        i_cols = st.columns(3)
        with i_cols[0]:
            i_window = st.selectbox("Window", ["Weekly", "Monthly", "Daily"], key="infl_window")
            i_window_days = {"Daily": 1, "Weekly": 7, "Monthly": 30}[i_window]
        with i_cols[1]:
            i_category = st.selectbox("Category", ["All"] + TAXONOMY, key="infl_category")
        with i_cols[2]:
            i_sort = st.selectbox("Sort by", ["Breadth (hashtags)", "Followers"], key="infl_sort")

        influencers = db.get_top_influencers(
            conn_i, i_window_days,
            category=None if i_category == "All" else i_category,
        )
        if i_sort == "Followers":
            influencers = sorted(influencers, key=lambda r: r["follower_count"], reverse=True)

        st.caption(f"{len(influencers)} creators")
        st.dataframe(
            [
                {
                    "creator": f"@{r['handle']}",
                    "name": r["nickname"],
                    "followers": r["follower_count"],
                    "trending hashtags": r["hashtag_count"],
                    "categories": ", ".join(r["categories"]),
                    "appears on": ", ".join(r["hashtags"][:8]),
                    "surfacing": ", ".join(r["surfacing_products"][:8]),
                }
                for r in influencers[:100]
            ],
            width="stretch",
            hide_index=True,
            column_config={
                "creator": st.column_config.TextColumn("creator"),
                "followers": st.column_config.NumberColumn("followers", format="%d"),
            },
        )

with predict_tab:
    st.subheader("Forward-growth prediction")
    st.warning(
        "**Experimental prototype.** This model predicts whether a hashtag's "
        "adoption (video_count) will keep growing by the next capture, from the "
        "shape of its early popularity curve. It trains only on real captured "
        "data -- no fabricated data. At the current tiny sample it is NOT yet "
        "reliable; the honest metrics are shown below so you can judge for "
        "yourself. It exists now so it can improve automatically as the daily "
        "cron accumulates history."
    )

    from src import predict, trajectory

    conn_p = db.connect()
    Xf, yf, _ = trajectory.build_forward_dataset(conn_p)
    metrics = predict.evaluate(Xf, yf)

    if metrics.get("skipped"):
        st.info(f"Not enough data to evaluate yet: {metrics['reason']}.")
    else:
        st.markdown("**Honest out-of-sample metrics (5-fold cross-validated):**")
        m_cols = st.columns(4)
        m_cols[0].metric("ROC-AUC", metrics["roc_auc"], help="0.5 = coin flip, 1.0 = perfect")
        m_cols[1].metric("F1 (growth class)", metrics["f1"])
        m_cols[2].metric("Accuracy", metrics["accuracy"], f"baseline {metrics['baseline_accuracy']}")
        m_cols[3].metric("Examples", metrics["n"], f"{metrics['positives']} positive")
        if metrics["roc_auc"] < 0.65:
            st.caption(
                "ROC-AUC is close to 0.5 -- the model barely beats chance at this "
                "data volume. Treat the probabilities below as illustrative of the "
                "pipeline, not as trustworthy calls yet."
            )

        latest = conn_p.execute("SELECT MAX(predicted_date) FROM predictions").fetchone()[0]
        if latest is not None:
            rows = conn_p.execute(
                """
                SELECT t.name, t.category, p.growth_probability,
                       COALESCE(m.stage, 'new') AS stage
                FROM predictions p
                JOIN trends t ON t.id = p.trend_id
                LEFT JOIN metrics m ON m.trend_id = t.id AND m.computed_date = p.predicted_date
                WHERE p.predicted_date = %s
                ORDER BY p.growth_probability DESC
                """,
                (latest,),
            ).fetchall()
            st.markdown(f"**Predicted growth probability for {latest}:**")
            st.dataframe(
                [
                    {"trend": n, "category": c or "other",
                     "growth probability": round(pr, 3), "current stage": stg}
                    for n, c, pr, stg in rows
                ],
                width="stretch", hide_index=True,
            )

with about_tab:
    st.markdown(
        """
### What this is

TrendRadar tracks trending TikTok hashtags over time and tags each one with a
topic and a lifecycle stage. The bet behind the whole project: a single
snapshot of "what's trending today" is far less useful than watching the
*trajectory* -- how fast a trend is growing, and whether that growth is
speeding up or slowing down.

### How it works, once a day, automatically

1. **Capture** -- pull trending hashtags from TikTok's public Creative Center.
2. **Categorize** -- Claude tags each hashtag with a topic (music, food, tech, ...).
3. **Measure** -- once a hashtag has enough daily history, compute its growth
   rate (velocity), whether growth is accelerating, and a lifecycle stage.
4. **Surface** -- this dashboard.

A GitHub Actions cron job runs this every day at 06:00 UTC. Nobody needs to
run anything by hand for it to keep working.

### What the stage badges mean

| Stage | Meaning |
|---|---|
| ⚪ new | Fewer than 4 days of history -- too early to say anything yet |
| 🟢 rising | Growing at least 5%/day |
| 🟡 cresting | Still growing but slowing down, or flat at the top |
| 🔴 declining | Shrinking at least 5%/day |
| ⚫ dormant | Hasn't shown up in the daily capture for 3+ days |

### Honest limitations

- **No trending sounds yet.** TikTok's own sounds-trends page has no data
  right now ("coming soon" on TikTok's end -- not a scraping failure).
- **No age/audience demographics.** That data requires a logged-in TikTok Ads
  session, which this project deliberately avoids to stay a no-login, low-risk
  scraper. The column stays empty rather than showing made-up numbers.
- **The hashtag list isn't TikTok's literal "top 100."** That full ranked
  view also requires login. Instead, this assembles hashtags from TikTok's
  public per-industry "breakout" signal -- arguably a better fit for catching
  things early anyway, which is the whole point.
- **History is still short.** Velocity/acceleration/stage need several real
  days of data to mean anything, so most trends will read "new" for a while.
  That's expected, not broken.

See `README.md` for technical setup, and `PROJECT_PLAN.md` /
`CLAUDE_CODE_PHASE1.md` / `CLAUDE_CODE_PHASE2.md` for the full build specs.
        """
    )
