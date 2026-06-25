"""
Synthetic validation for src/metrics.py (CLAUDE_CODE_PHASE2.md sec 7). Real
snapshot history is too thin to exercise the staging logic yet, so these
hand-built curves prove the math before it's trusted against live data.
Pure-function tests -- no database touched.
"""

import datetime
import random

from src.metrics import classify_stage

TODAY = datetime.date(2026, 6, 25)


def _dates(n, end=TODAY):
    """n consecutive dates ending at `end`."""
    return [end - datetime.timedelta(days=n - 1 - i) for i in range(n)]


def test_clean_riser():
    dates = _dates(21)
    values = [100.0]
    for _ in range(20):
        values.append(values[-1] * 1.15)  # steady 15%/day growth, no deceleration
    result = classify_stage(list(zip(dates, values)), TODAY)
    assert result["stage"] == "rising"
    assert result["velocity"] > 0


def test_peaker():
    dates = _dates(21)
    growth_rates = [0.15] * 15 + [0.10, 0.06, 0.03, 0.015, 0.005]  # rise, then decelerate
    values = [100.0]
    for r in growth_rates:
        values.append(values[-1] * (1 + r))
    result = classify_stage(list(zip(dates, values)), TODAY)
    assert result["stage"] == "cresting"
    assert result["acceleration"] is not None and result["acceleration"] < 0
    assert result["velocity"] > 0  # still growing, just decelerating


def test_decliner():
    dates = _dates(21)
    values = [10000.0]
    for _ in range(20):
        values.append(values[-1] * 0.85)  # steady 15%/day shrinkage
    result = classify_stage(list(zip(dates, values)), TODAY)
    assert result["stage"] == "declining"
    assert result["velocity"] < 0


def test_flat_plateau():
    dates = _dates(21)
    values = [5000.0] * 21
    result = classify_stage(list(zip(dates, values)), TODAY)
    assert result["stage"] == "cresting"


def test_stale_dormant():
    dates = _dates(21, end=TODAY - datetime.timedelta(days=5))
    values = [100.0]
    for _ in range(20):
        values.append(values[-1] * 1.15)
    result = classify_stage(list(zip(dates, values)), TODAY)
    assert result["stage"] == "dormant"
    assert result["velocity"] is None


def test_too_short_is_new():
    dates = _dates(3)
    values = [100.0, 150.0, 200.0]
    result = classify_stage(list(zip(dates, values)), TODAY)
    assert result["stage"] == "new"
    assert result["velocity"] is None
    assert result["acceleration"] is None


def test_noisy_riser_still_rises():
    rng = random.Random(42)
    dates = _dates(21)
    values = []
    for i in range(21):
        base = 100.0 * (1.12 ** i)
        jitter = base * rng.uniform(-0.15, 0.15)
        values.append(base + jitter)
    result = classify_stage(list(zip(dates, values)), TODAY)
    assert result["stage"] == "rising"  # smoothing should absorb the day-to-day jitter
