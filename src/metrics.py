"""
Pure functions for trend velocity/acceleration/lifecycle staging. No database
access here on purpose -- everything takes plain data in and returns plain
data out, so it's unit-testable in isolation (see tests/test_metrics.py).

The math is specified exactly in CLAUDE_CODE_PHASE2.md sec 3 -- this module
implements that spec, it doesn't reinterpret it.
"""

MIN_SNAPSHOTS = 4        # below this -> stage "new"
SMOOTH_WINDOW = 3         # trailing moving-average window
VELOCITY_RISING = 0.05    # >= +5%/day counts as rising
VELOCITY_DECLINING = -0.05  # <= -5%/day counts as declining
ACCEL_DECEL = -0.02       # acceleration below this = meaningfully decelerating
DORMANT_DAYS = 3          # no snapshot in this many days -> dormant

STAGES = ("new", "rising", "cresting", "declining", "dormant")


def smooth(values, window=SMOOTH_WINDOW):
    """
    Trailing moving average. Returns len(values) - window + 1 points (or []
    if there aren't enough values), one per original index from `window - 1`
    onward. E.g. for 4 values and window=3, returns 2 smoothed points.
    """
    if len(values) < window:
        return []
    return [sum(values[i - window + 1:i + 1]) / window for i in range(window - 1, len(values))]


def velocity(smoothed, day_gaps):
    """
    Relative growth rate (fraction per day) between the two most recent
    smoothed points, normalized by the actual day gap (day_gaps[-1]) so a
    missing day doesn't distort the rate. None if there aren't two smoothed
    points yet, or the earlier one is <= 0 (avoids divide-by-zero / nonsense
    on a zero-base trend).
    """
    if len(smoothed) < 2 or not day_gaps:
        return None
    prev, curr = smoothed[-2], smoothed[-1]
    if prev <= 0:
        return None
    delta_days = max(day_gaps[-1], 1)
    return ((curr - prev) / prev) / delta_days


def acceleration(smoothed, day_gaps):
    """
    Change in velocity: the most recent velocity reading minus the one
    before it. None if there aren't enough smoothed points for two velocity
    readings (needs 3 smoothed points, i.e. 5 raw snapshots at window=3).
    """
    if len(smoothed) < 3 or len(day_gaps) < 2:
        return None
    v_curr = velocity(smoothed, day_gaps)
    v_prev = velocity(smoothed[:-1], day_gaps[:-1])
    if v_curr is None or v_prev is None:
        return None
    return v_curr - v_prev


def classify_stage(snapshots, today):
    """
    snapshots: list of (captured_date, count) for one trend, any order.
    today: the date to evaluate dormancy against (the pipeline's run date).

    Returns {smoothed_count, velocity, acceleration, stage, snapshot_count}.
    Deterministic decision tree, first match wins -- see
    CLAUDE_CODE_PHASE2.md sec 3.4. A velocity of None (zero-base guard) is
    treated as neither rising nor declining and falls through to "cresting",
    the spec's catch-all bucket -- the spec doesn't address this edge case
    explicitly, but null-out-rather-than-guess (sec 9) rules out treating it
    as a confident rise or decline.
    """
    ordered = sorted(snapshots, key=lambda row: row[0])
    n = len(ordered)

    if n < MIN_SNAPSHOTS:
        return {"smoothed_count": None, "velocity": None, "acceleration": None,
                "stage": "new", "snapshot_count": n}

    dates = [d for d, _ in ordered]
    values = [float(v) for _, v in ordered]
    smoothed = smooth(values)
    latest_smoothed = smoothed[-1] if smoothed else None

    if (today - dates[-1]).days > DORMANT_DAYS:
        return {"smoothed_count": latest_smoothed, "velocity": None, "acceleration": None,
                "stage": "dormant", "snapshot_count": n}

    day_gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
    smoothed_day_gaps = day_gaps[SMOOTH_WINDOW - 1:]

    vel = velocity(smoothed, smoothed_day_gaps)
    accel = acceleration(smoothed, smoothed_day_gaps)

    if vel is not None and vel <= VELOCITY_DECLINING:
        stage = "declining"
    elif accel is not None and accel < ACCEL_DECEL and vel is not None and vel > 0:
        stage = "cresting"
    elif vel is not None and vel >= VELOCITY_RISING:
        stage = "rising"
    else:
        stage = "cresting"

    return {"smoothed_count": latest_smoothed, "velocity": vel, "acceleration": accel,
            "stage": stage, "snapshot_count": n}
