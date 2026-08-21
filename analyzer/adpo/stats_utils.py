"""
Small, dependency-free statistics helpers shared by every analyzer.

Kept deliberately minimal (stdlib `statistics` only, no numpy/pandas) so
the whole engine has zero third-party runtime dependencies.
"""
from __future__ import annotations

from statistics import mean as _mean
from statistics import median as _median  # noqa: F401 (re-exported for convenience)
from statistics import stdev as _stdev
from typing import List, Optional


def mean(values: List[float]) -> float:
    return _mean(values)


def stdev(values: List[float]) -> Optional[float]:
    """Sample standard deviation. None if fewer than 2 points (undefined)."""
    if len(values) < 2:
        return None
    return _stdev(values)


def coefficient_of_variation(values: List[float]) -> Optional[float]:
    """stdev / mean - a scale-free measure of run-to-run variability.

    Used throughout the analyzers to distinguish "consistently slow" (low
    CV - no empirical evidence a faster run is achievable) from
    "variably slow" (high CV - some runs were meaningfully faster than
    others, which is itself evidence of achievable improvement, e.g. from
    caching or reduced contention).
    """
    if len(values) < 2:
        return None
    m = _mean(values)
    if m == 0:
        return None
    sd = _stdev(values)
    return sd / m


def percentile(values: List[float], pct: float) -> float:
    """Linear-interpolation percentile (same convention as numpy's default)."""
    if not values:
        raise ValueError("percentile() arg is an empty sequence")
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    d0 = s[f] * (c - k)
    d1 = s[c] * (k - f)
    return d0 + d1


def safe_divide(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b else default


def linear_trend_slope(values: List[float]) -> Optional[float]:
    """Least-squares slope of `values` against their index (0..n-1).

    Positive slope => trending up (getting slower) over the observed
    sequence. Used as a *supplementary* signal in the regression analyzer
    (in addition to the primary baseline-vs-recent-window comparison),
    since a monotonic drift can be a real regression even when it hasn't
    yet produced a sharp jump between two windows.
    """
    n = len(values)
    if n < 2:
        return None
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(values) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, values))
    den = sum((x - mean_x) ** 2 for x in xs)
    if den == 0:
        return None
    return num / den
