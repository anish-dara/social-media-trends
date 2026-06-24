"""
Read-only Streamlit dashboard: today's top trends by category, age
demographics where available, and a per-trend "days tracked" counter.
No writes happen here -- the pipeline owns all inserts.
"""

import streamlit as st

from src import db

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
        t.name,
        t.category,
        t.demographics,
        s.rank,
        s.video_count,
        s.view_count,
        (SELECT COUNT(DISTINCT captured_date) FROM snapshots s2 WHERE s2.trend_id = t.id) AS days_tracked
    FROM snapshots s
    JOIN trends t ON t.id = s.trend_id
    WHERE s.captured_date = %s
    ORDER BY s.rank
    """,
    (latest_date,),
).fetchall()

records = [
    {
        "rank": rank,
        "name": name,
        "category": category or "other",
        "video_count": video_count,
        "view_count": view_count,
        "days_tracked": days_tracked,
    }
    for name, category, _demographics, rank, video_count, view_count, days_tracked in rows
]

categories = ["All"] + sorted({r["category"] for r in records})
selected = st.selectbox("Category", categories)

filtered = records if selected == "All" else [r for r in records if r["category"] == selected]

st.dataframe(filtered, use_container_width=True, hide_index=True)

with st.expander("Age demographics"):
    with_demographics = [
        (name, demographics) for name, _c, demographics, *_ in rows if demographics
    ]
    if with_demographics:
        for name, demographics in with_demographics:
            st.write(name, demographics)
    else:
        st.write(
            "No age demographic data available. Creative Center's per-hashtag "
            "demographics endpoint requires a logged-in TikTok Ads session, which "
            "this project deliberately avoids -- see PROJECT_PLAN.md."
        )
