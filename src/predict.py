"""
Phase 5 (Predict) -- model + honest evaluation.

Trains a simple, interpretable classifier on the REAL forward-trajectory data
from trajectory.py to answer: given a hashtag's early curve shape today, will
its adoption (video_count) keep growing by the next capture?

Deliberately simple (logistic regression, class-balanced) because the dataset
is small and imbalanced -- a deep model would just overfit and lie. Every
metric is out-of-sample (cross-validated) and compared against a majority-class
baseline, so the numbers don't flatter themselves. At current volume this is a
PROTOTYPE: the pipeline is real and the data is real, but the sample is small
enough that the model's job right now is to exist and improve as the cron
accumulates history -- not to be trusted as a finished predictor.
"""

import numpy as np
from sklearn.dummy import DummyClassifier
from sklearn.feature_extraction import DictVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from src import db as db_module
from src import trajectory

MIN_EXAMPLES = 40  # below this, don't pretend to evaluate -- too few to say anything
MODEL_VERSION = "forward-logreg-v1"  # bump when features/model change


def _model():
    # Scale (logistic reg likes it) + balanced weights for the ~28% positive class.
    return make_pipeline(
        DictVectorizer(sparse=False),
        StandardScaler(),
        LogisticRegression(max_iter=1000, class_weight="balanced"),
    )


def evaluate(X, y):
    """
    Honest out-of-sample evaluation via stratified 5-fold cross-validation,
    against a majority-class baseline. Returns a dict of metrics (or a reason
    it was skipped). Never trains and tests on the same rows.
    """
    y = np.array(y)
    n, pos = len(y), int(y.sum())
    if n < MIN_EXAMPLES or pos < 5 or (n - pos) < 5:
        return {"skipped": True, "reason": f"too few examples (n={n}, positives={pos})"}

    folds = min(5, pos)  # can't have more folds than positives
    cv = StratifiedKFold(n_splits=folds, shuffle=True, random_state=0)

    pred = cross_val_predict(_model(), X, y, cv=cv, method="predict")
    proba = cross_val_predict(_model(), X, y, cv=cv, method="predict_proba")[:, 1]

    baseline = DummyClassifier(strategy="most_frequent").fit(X, y).predict(X)

    return {
        "skipped": False,
        "n": n,
        "positives": pos,
        "positive_rate": round(pos / n, 3),
        "cv_folds": folds,
        "accuracy": round(accuracy_score(y, pred), 3),
        "baseline_accuracy": round(accuracy_score(y, baseline), 3),
        "precision": round(precision_score(y, pred, zero_division=0), 3),
        "recall": round(recall_score(y, pred, zero_division=0), 3),
        "f1": round(f1_score(y, pred, zero_division=0), 3),
        "roc_auc": round(roc_auc_score(y, proba), 3),
    }


def train_forward_model(conn):
    """Fit the forward-growth model on all available real data. Returns the
    fitted pipeline, or None if there's not enough data yet."""
    X, y, _ = trajectory.build_forward_dataset(conn)
    if len(y) < MIN_EXAMPLES or sum(y) < 5:
        return None
    model = _model()
    model.fit(X, y)
    return model


def score_today(conn, model, captured_date):
    """
    Growth probability for each hashtag captured on `captured_date`, using the
    same early-curve features the model was trained on. Returns list of
    {trend_id, name, growth_probability}. Empty if model is None.
    """
    if model is None:
        return []
    rows = conn.execute(
        """
        SELECT t.id, t.name, s.raw_json, s.rank
        FROM snapshots s JOIN trends t ON t.id = s.trend_id
        WHERE s.captured_date = %s AND s.raw_json IS NOT NULL
        """,
        (captured_date,),
    ).fetchall()
    feats, meta = [], []
    for tid, name, raw, rank in rows:
        curve = [p.get("value") for p in (raw or {}).get("popularityCurve", [])
                 if p.get("value") is not None]
        if len(curve) < 7:
            continue
        f = trajectory.curve_features(curve[:7])
        f["log_rank"] = float(np.log1p(rank or 0))
        feats.append(f)
        meta.append({"trend_id": tid, "name": name})
    if not feats:
        return []
    probs = model.predict_proba(feats)[:, 1]
    return [{**m, "growth_probability": float(round(p, 3))} for m, p in zip(meta, probs)]


def compute_and_store(conn, predicted_date):
    """
    Train on all real data so far, score `predicted_date`'s trends, and upsert
    the growth probabilities. Also records today's out-of-sample AUC to
    model_metrics so quality can be tracked over time. Returns the number of
    predictions stored (0 if too little data to train yet). Called from the
    daily pipeline, wrapped so a failure here never touches ingestion/metrics.
    """
    model = train_forward_model(conn)
    scored = score_today(conn, model, predicted_date)
    for s in scored:
        db_module.upsert_prediction(conn, s["trend_id"], predicted_date,
                                    s["growth_probability"], MODEL_VERSION)

    # Track model quality for this day (skipped-metrics rows aren't stored).
    Xf, yf, _ = trajectory.build_forward_dataset(conn, as_of=predicted_date)
    metrics = evaluate(Xf, yf)
    if not metrics.get("skipped"):
        db_module.upsert_model_metrics(conn, predicted_date, MODEL_VERSION, metrics)
    return len(scored)


def backfill_model_metrics(conn):
    """
    One-off: reconstruct the forward dataset as of each past capture day and
    record its out-of-sample AUC, so the tracking chart shows a real history
    (how quality moved as data accumulated) instead of starting from today.
    Honest -- each day is evaluated only on data that existed by then. Returns
    the number of days stored.
    """
    dates = [d for (d,) in conn.execute(
        "SELECT DISTINCT captured_date FROM snapshots ORDER BY captured_date"
    ).fetchall()]
    stored = 0
    for d in dates:
        Xf, yf, _ = trajectory.build_forward_dataset(conn, as_of=d)
        metrics = evaluate(Xf, yf)
        if not metrics.get("skipped"):
            db_module.upsert_model_metrics(conn, d, MODEL_VERSION, metrics)
            stored += 1
    return stored


if __name__ == "__main__":
    from src import db
    conn = db.connect()

    print("=== FORWARD model (real target: will video_count grow by next capture?) ===")
    Xf, yf, _ = trajectory.build_forward_dataset(conn)
    forward_metrics = evaluate(Xf, yf)
    for k, v in forward_metrics.items():
        print(f"  {k}: {v}")

    print("\n=== SHAPE model (weaker: is the curve still rising in its tail?) ===")
    Xs, ys, _ = trajectory.build_shape_dataset(conn)
    for k, v in evaluate(Xs, ys).items():
        print(f"  {k}: {v}")

    if not forward_metrics.get("skipped"):
        print("\n=== today's growth predictions (top 10 by probability) ===")
        model = train_forward_model(conn)
        latest = conn.execute("SELECT MAX(captured_date) FROM snapshots").fetchone()[0]
        scored = sorted(score_today(conn, model, latest),
                        key=lambda r: r["growth_probability"], reverse=True)
        for s in scored[:10]:
            print(f"  {s['growth_probability']:.2f}  {s['name']}")
