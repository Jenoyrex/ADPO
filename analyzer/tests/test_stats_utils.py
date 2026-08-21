from adpo.stats_utils import (
    coefficient_of_variation,
    linear_trend_slope,
    percentile,
    safe_divide,
    stdev,
)


def test_stdev_needs_two_points():
    assert stdev([5]) is None
    assert stdev([]) is None
    assert stdev([1, 2, 3]) is not None


def test_coefficient_of_variation_zero_mean():
    assert coefficient_of_variation([0, 0, 0]) is None


def test_coefficient_of_variation_basic():
    cv = coefficient_of_variation([100, 100, 100])
    assert cv == 0
    cv2 = coefficient_of_variation([50, 100, 150])
    assert cv2 > 0


def test_percentile_matches_known_values():
    values = [10, 20, 30, 40, 50]
    assert percentile(values, 0) == 10
    assert percentile(values, 100) == 50
    assert percentile(values, 50) == 30


def test_percentile_single_value():
    assert percentile([42], 90) == 42


def test_percentile_empty_raises():
    try:
        percentile([], 50)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_safe_divide():
    assert safe_divide(10, 2) == 5
    assert safe_divide(10, 0) == 0.0
    assert safe_divide(10, 0, default=-1) == -1


def test_linear_trend_slope_increasing():
    slope = linear_trend_slope([10, 20, 30, 40, 50])
    assert slope == 10


def test_linear_trend_slope_flat():
    slope = linear_trend_slope([10, 10, 10, 10])
    assert slope == 0


def test_linear_trend_slope_needs_two_points():
    assert linear_trend_slope([5]) is None
    assert linear_trend_slope([]) is None
