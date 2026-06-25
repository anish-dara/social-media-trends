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

import streamlit as st

from src import db

STAGE_EMOJI = {"new": "⚪", "rising": "\U0001f7e2", "cresting": "\U0001f7e1",
               "declining": "\U0001f534", "dormant": "⚫"}


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
        st.caption(f"Showing trends captured on {latest_date}")

        rows = conn.execute(
            """
            SELECT
                t.id, t.name, t.category, t.demographics,
                s.rank, s.video_count, s.view_count,
                m.stage, m.velocity, m.acceleration,
                (SELECT COUNT(DISTINCT captured_date) FROM snapshots s2 WHERE s2.trend_id = t.id) AS days_tracked
            FROM snapshots s
            JOIN trends t ON t.id = s.trend_id
            LEFT JOIN metrics m ON m.trend_id = t.id AND m.computed_date = s.captured_date
            WHERE s.captured_date = %s
            ORDER BY s.rank
            """,
            (latest_date,),
        ).fetchall()

        trend_ids = [row[0] for row in rows]
        history_rows = conn.execute(
            "SELECT trend_id, smoothed_count FROM metrics WHERE trend_id = ANY(%s) ORDER BY computed_date",
            (trend_ids,),
        ).fetchall()
        history_by_trend = {}
        for trend_id, smoothed in history_rows:
            if smoothed is not None:
                history_by_trend.setdefault(trend_id, []).append(smoothed)

        records = []
        for trend_id, name, category, demographics, rank, video_count, view_count, stage, velocity, acceleration, days_tracked in rows:
            stage = stage or "new"
            if velocity is None:
                velocity_display = f"history building -- {days_tracked} day{'s' if days_tracked != 1 else ''} tracked"
            else:
                velocity_display = f"{velocity:+.1%}/day"

            records.append({
                "rank": rank,
                "name": name,
                "category": category or "other",
                "stage": f"{STAGE_EMOJI.get(stage, '')} {stage}",
                "velocity": velocity_display,
                "smoothed_history": history_by_trend.get(trend_id, []),
                "video_count": video_count,
                "view_count": view_count,
                "_stage_raw": stage,
                "_velocity_raw": velocity,
            })

        categories = ["All"] + sorted({r["category"] for r in records})
        selected = st.selectbox("Category", categories)
        filtered = records if selected == "All" else [r for r in records if r["category"] == selected]

        st.subheader("Today's trends")
        st.dataframe(
            _display(filtered),
            width="stretch",
            hide_index=True,
            column_config={"smoothed_history": st.column_config.LineChartColumn("Smoothed history")},
        )

        st.subheader("Rising now")
        rising = sorted(
            (r for r in records if r["_stage_raw"] == "rising"),
            key=lambda r: r["_velocity_raw"],
            reverse=True,
        )
        if rising:
            st.dataframe(
                _display(rising),
                width="stretch",
                hide_index=True,
                column_config={"smoothed_history": st.column_config.LineChartColumn("Smoothed history")},
            )
        else:
            st.write("Nothing is rising yet -- history is still building (see stage badges above).")

        with st.expander("Age demographics"):
            with_demographics = [(name, demographics) for _id, name, _c, demographics, *_ in rows if demographics]
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
