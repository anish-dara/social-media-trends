"""
Phase 5 (Predict) -- data foundation. Turns the real 7-day popularityCurve
that TikTok returns for every hashtag (stored in snapshots.raw_json since
Phase 1) into training examples. NO synthetic/fabricated data here: every
row is a real captured trajectory. Synthetic curves live only in the tests,
to exercise the feature math.

Two datasets, both real:

  build_shape_dataset  -- one example per stored curve. Features come from the
      early part of the 7-point curve; label is whether the curve is still
      rising in its tail. Lots of samples, but it only describes a curve's own
      shape -- weak "prediction", useful mainly for volume + plumbing.

  build_forward_dataset -- the real target. One example per (snapshot, its next
      capture of the same hashtag). Features are that day's curve + rank; label
      is whether the hashtag's video_count actually grew by the next capture.
      Fewer samples, but it's genuine forward-looking ground truth and it grows
      every day the cron runs.

Everything is a prototype at current data volume -- see predict.py for the
honest evaluation caveats.
"""

import numpy as np

# A snapshot's curve is 7 daily normalized (0-100) popularity points. Features
# read the early shape; the tail is used only for the shape-dataset label.
EARLY_POINTS = 4
FORWARD_GROWTH_THRESHOLD = 0.02  # >2% video_count growth by next capture = "growing"


def curve_features(curve):
    """
    Feature dict from a 7-point popularity curve (list of floats, oldest first).
    Pure function -- unit-tested on synthetic curves in tests/test_trajectory.py.
    Uses only the first EARLY_POINTS points so the same features are honest as
    an *early* signal (no peeking at the tail we're trying to predict).
    """
    c = np.array(curve, dtype=float)
    early = c[:EARLY_POINTS]
    diffs = np.diff(early)
    peak_idx = int(np.argmax(early))
    return {
        "value_start": float(early[0]),
        "value_early_end": float(early[-1]),
        "early_slope": float((early[-1] - early[0]) / (len(early) - 1)),
        "recent_slope": float(diffs[-1]) if len(diffs) else 0.0,
        "mean_step": float(diffs.mean()) if len(diffs) else 0.0,
        "volatility": float(diffs.std()) if len(diffs) else 0.0,
        "peak_position": float(peak_idx / (len(early) - 1)) if len(early) > 1 else 0.0,
        "monotonic_frac": float((diffs > 0).mean()) if len(diffs) else 0.0,
        "area": float(early.mean()),
    }


def _load_curve_rows(conn, as_of=None):
    """
    Real stored curves, ordered so each trend's captures are consecutive.
    `as_of` bounds to captured_date <= as_of -- used to reconstruct the dataset
    as it looked on a past day, so the AUC can be backfilled honestly (no
    peeking at data that didn't exist yet).
    """
    rows = conn.execute(
        """
        SELECT t.id, t.name, t.category, s.captured_date, s.raw_json,
               s.primary_metric, s.rank
        FROM snapshots s JOIN trends t ON t.id = s.trend_id
        WHERE s.raw_json IS NOT NULL
          AND (%s::date IS NULL OR s.captured_date <= %s::date)
        ORDER BY t.id, s.captured_date
        """,
        (as_of, as_of),
    ).fetchall()
    out = []
    for tid, name, cat, date, raw, metric, rank in rows:
        # popularityCurve is TikTok-only; YouTube snapshots lack it and are
        # naturally excluded here, so the predictor stays TikTok-only for now.
        curve = [p.get("value") for p in (raw or {}).get("popularityCurve", [])
                 if p.get("value") is not None]
        if len(curve) >= 7:
            out.append({"trend_id": tid, "name": name, "category": cat,
                        "date": date, "curve": curve[:7], "metric": metric, "rank": rank})
    return out


def build_shape_dataset(conn):
    """
    One example per curve. Label = curve still rising in its tail (last point >
    the early window's end). Returns (feature_dicts, labels, meta).
    """
    rows = _load_curve_rows(conn)
    X, y, meta = [], [], []
    for r in rows:
        c = r["curve"]
        feats = curve_features(c)
        label = int(c[-1] > c[EARLY_POINTS - 1])  # tail higher than early end?
        X.append(feats)
        y.append(label)
        meta.append({"name": r["name"], "date": str(r["date"])})
    return X, y, meta


def build_forward_dataset(conn, as_of=None):
    """
    The real forward target. For each snapshot that has a *next* capture of the
    same hashtag, features = that day's early-curve shape + log rank; label =
    did video_count grow by more than FORWARD_GROWTH_THRESHOLD by the next
    capture. `as_of` reconstructs the dataset as of a past day (for AUC
    backfill). Returns (feature_dicts, labels, meta).
    """
    rows = _load_curve_rows(conn, as_of=as_of)
    by_trend = {}
    for r in rows:
        by_trend.setdefault(r["trend_id"], []).append(r)

    X, y, meta = [], [], []
    for captures in by_trend.values():
        captures.sort(key=lambda r: r["date"])
        for cur, nxt in zip(captures, captures[1:]):
            if not cur["metric"] or cur["metric"] <= 0:
                continue
            feats = curve_features(cur["curve"])
            feats["log_rank"] = float(np.log1p(cur["rank"] or 0))
            growth = (nxt["metric"] - cur["metric"]) / cur["metric"]
            X.append(feats)
            y.append(int(growth > FORWARD_GROWTH_THRESHOLD))
            meta.append({"name": cur["name"], "from": str(cur["date"]),
                         "to": str(nxt["date"]), "growth": round(growth, 4)})
    return X, y, meta


if __name__ == "__main__":
    from collections import Counter
    from src import db
    conn = db.connect()

    Xs, ys, _ = build_shape_dataset(conn)
    print(f"shape dataset:   {len(Xs)} examples, label balance {Counter(ys)}")

    Xf, yf, _ = build_forward_dataset(conn)
    print(f"forward dataset: {len(Xf)} examples, label balance {Counter(yf)}")
    if Xf:
        print("forward feature keys:", list(Xf[0].keys()))
