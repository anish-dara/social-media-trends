"""
Read-only Streamlit dashboard: today's top trends by category, age
demographics where available, lifecycle stage badges with a smoothed-count
sparkline, and a "Rising now" view. No writes happen here -- pipeline.py and
compute_metrics.py own all inserts.
"""

import streamlit as st

from src import db

STAGE_EMOJI = {"new": "⚪", "rising": "\U0001f7e2", "cresting": "\U0001f7e1",
               "declining": "\U0001f534", "dormant": "⚫"}


def _display(records):
    """Strip internal sort/filter-only fields before handing rows to st.dataframe."""
    return [{k: v for k, v in r.items() if not k.startswith("_")} for r in records]


st.set_page_config(page_title="TrendRadar", layout="wide")
st.title("TrendRadar")

conn = db.connect()
latest_date = conn.execute("SELECT MAX(captured_date) FROM snapshots").fetchone()[0]

if latest_date is None:
    st.info("No snapshots yet -- run `python -m src.pipeline` to capture today's trends.")
    st.stop()

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
    use_container_width=True,
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
        use_container_width=True,
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
