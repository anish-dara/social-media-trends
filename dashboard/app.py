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

trends_tab, about_tab = st.tabs(["Trends", "About"])

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
