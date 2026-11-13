"""
Small, dependency-free statistics helpers shared by every analyzer.

Kept deliberately minimal (stdlib `statistics` only, no numpy/pandas) so
the whole engine has zero third-party runtime dependencies.
"""

from __future__ import annotations

from math import exp, lgamma, log, log1p
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


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the regularized incomplete beta (modified Lentz)."""
    tiny, eps = 1e-300, 3e-16
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def beta_cdf(x: float, a: float, b: float) -> float:
    """Regularized incomplete beta I_x(a, b) = P(X <= x) for X ~ Beta(a, b). Stdlib only."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_front = lgamma(a + b) - lgamma(a) - lgamma(b) + a * log(x) + b * log1p(-x)
    if x < (a + 1.0) / (a + b + 2.0):
        return exp(log_front) * _betacf(a, b, x) / a
    return 1.0 - exp(log_front) * _betacf(b, a, 1.0 - x) / b


def beta_ppf(q: float, a: float, b: float) -> float:
    """Quantile of Beta(a, b) by bisection on `beta_cdf` (deterministic, ~1e-12 accurate)."""
    lo, hi = 0.0, 1.0
    for _ in range(100):
        mid = (lo + hi) / 2.0
        if beta_cdf(mid, a, b) < q:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def jeffreys_lower_bound(k: int, n: int, q: float = 0.05) -> float:
    """q-quantile of the Jeffreys posterior Beta(k + 0.5, n - k + 0.5) for k events in n trials.

    With q=0.05 this is a one-sided 95% credible lower bound on the event rate.
    """
    if n <= 0:
        return 0.0
    return beta_ppf(q, k + 0.5, n - k + 0.5)
