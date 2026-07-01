"""
Unit tests for the trajectory feature math (src/trajectory.py). These use
SYNTHETIC curves on purpose -- this is the legitimate use of fabricated data:
exercising the feature-extraction plumbing with known-shape inputs, NOT
training a model. The model itself only ever trains on real captured curves.
"""

from src.trajectory import curve_features, EARLY_POINTS


def test_rising_curve_has_positive_slope():
    f = curve_features([0, 20, 40, 60, 80, 90, 100])
    assert f["early_slope"] > 0
    assert f["monotonic_frac"] == 1.0        # every early step is up
    assert f["value_start"] == 0


def test_falling_curve_has_negative_slope():
    f = curve_features([100, 80, 60, 40, 20, 10, 0])
    assert f["early_slope"] < 0
    assert f["monotonic_frac"] == 0.0        # no early step is up


def test_features_only_use_early_points():
    # Two curves identical in the first EARLY_POINTS but divergent tails must
    # produce identical features -- the extractor must not peek at the tail.
    head = [10, 30, 50, 70]
    a = curve_features(head + [90, 95, 100])
    b = curve_features(head + [40, 20, 0])
    assert a == b


def test_peak_position_early_vs_late():
    early_peak = curve_features([100, 80, 60, 40, 30, 20, 10])
    late_peak = curve_features([10, 20, 40, 100, 60, 30, 10])
    assert early_peak["peak_position"] == 0.0        # max at first early point
    assert late_peak["peak_position"] == 1.0         # max at last early point


def test_flat_curve_zero_slope_zero_volatility():
    f = curve_features([50, 50, 50, 50, 50, 50, 50])
    assert f["early_slope"] == 0.0
    assert f["volatility"] == 0.0
    assert f["area"] == 50.0


def test_early_points_constant_matches_window():
    # Guards against the window silently changing under the tests.
    assert EARLY_POINTS == 4
