"""
Historical Regression Analyzer
================================
Full rationale in REASONING.md ("3. Historical Regression Analyzer").

Detects cases where a job (or the overall workflow) has reliably gotten
slower over time - as opposed to being slow-but-stable (handled by the
Slow Job / Slow Step analyzers) or having had a single one-off slow run
(explicitly guarded against here by comparing two *windows* of runs
rather than any two individual runs).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from statistics import median
from typing import Dict, List, Optional, Tuple

from ..models import Confidence, EstimatedSavings, Finding, Severity, WorkflowRun
from ..stats_utils import linear_trend_slope, percentile
from .base import BaseAnalyzer

WORKFLOW_TOTAL_KEY = "__workflow_total__"


@dataclass
class RegressionConfig:
    min_total_samples: int = 6
    min_baseline_samples: int = 3
    min_recent_samples: int = 3
    recent_fraction: float = 0.4
    min_pct_increase: float = 0.25
    min_absolute_increase_seconds: float = 20.0
    include_workflow_total: bool = True


class HistoricalRegressionAnalyzer(BaseAnalyzer):
    analyzer_type = "historical_regression"

    def __init__(self, config: RegressionConfig = None):
        self.config = config or RegressionConfig()

    def analyze(self, runs: List[WorkflowRun]) -> List[Finding]:
        findings: List[Finding] = []
        for _workflow_name, wf_runs in self._group_by_workflow(runs).items():
            findings.extend(self._analyze_single_workflow(wf_runs))
        return findings

    def _analyze_single_workflow(self, runs: List[WorkflowRun]) -> List[Finding]:
        cfg = self.config
        ordered = self._sorted_by_time(runs)

        series: Dict[str, List[Tuple[float, str]]] = defaultdict(list)
        for run in ordered:
            if cfg.include_workflow_total and run.conclusion == "success":
                d = run.duration_seconds
                if d is not None:
                    series[WORKFLOW_TOTAL_KEY].append((d, run.run_id))
            for job in run.jobs:
                if job.conclusion == "success" and job.duration_seconds is not None:
                    series[job.name].append((job.duration_seconds, run.run_id))

        findings: List[Finding] = []
        for key, points in series.items():
            finding = self._analyze_series(key, points)
            if finding:
                findings.append(finding)

        findings.sort(key=lambda f: f.metrics.get("absolute_increase_seconds", 0), reverse=True)
        return findings

    def _analyze_series(self, key: str, points: List[Tuple[float, str]]) -> Optional[Finding]:
        cfg = self.config
        n = len(points)
        if n < cfg.min_total_samples:
            return None

        durations = [p[0] for p in points]
        run_ids = [p[1] for p in points]

        recent_n = max(cfg.min_recent_samples, round(n * cfg.recent_fraction))
        recent_n = min(recent_n, n - cfg.min_baseline_samples)
        if recent_n < cfg.min_recent_samples:
            return None
        baseline_n = n - recent_n
        if baseline_n < cfg.min_baseline_samples:
            return None

        baseline = durations[:baseline_n]
        recent = durations[baseline_n:]
        baseline_run_ids = run_ids[:baseline_n]
        recent_run_ids = run_ids[baseline_n:]

        baseline_median = median(baseline)
        recent_median = median(recent)
        if baseline_median <= 0:
            return None

        pct_change = (recent_median - baseline_median) / baseline_median
        abs_change = recent_median - baseline_median
        if pct_change < cfg.min_pct_increase or abs_change < cfg.min_absolute_increase_seconds:
            return None

        baseline_p75 = percentile(baseline, 75)
        consistent_count = sum(1 for d in recent if d > baseline_p75)
        consistency = consistent_count / len(recent)

        slope = linear_trend_slope(durations)
        severity = self._severity(pct_change)
        confidence = self._confidence(baseline_n, recent_n, consistency)

        subject = "Overall workflow duration" if key == WORKFLOW_TOTAL_KEY else f"Job '{key}'"

        evidence = [
            f"{subject}: baseline median was {baseline_median:.0f}s over the first "
            f"{baseline_n} observed run(s) (e.g. {', '.join(baseline_run_ids[:3])}).",
            f"{subject}: recent median is {recent_median:.0f}s over the most recent "
            f"{recent_n} run(s) (e.g. {', '.join(recent_run_ids[:3])}).",
            f"That is a {pct_change*100:.0f}% increase ({abs_change:.0f}s), and "
            f"{consistent_count}/{recent_n} recent runs exceeded the 75th percentile of "
            f"baseline performance ({baseline_p75:.0f}s) - i.e. the slowdown is not "
            f"attributable to a single outlier run.",
        ]

        savings = EstimatedSavings(
            low_seconds=round(abs_change, 1),
            high_seconds=round(recent_median - min(baseline), 1),
            unknown=False,
            basis=(
                f"Low end: returning to the pre-regression baseline MEDIAN "
                f"({baseline_median:.0f}s), a level this "
                f"{'workflow' if key == WORKFLOW_TOTAL_KEY else 'job'} reliably achieved "
                f"before. High end: returning to the best time observed during the "
                f"baseline period ({min(baseline):.0f}s)."
            ),
        )

        return Finding(
            analyzer_type=self.analyzer_type,
            title=f"{subject} has regressed compared to its own history",
            description=(
                f"{subject} is reliably running slower now than it did earlier in the "
                f"observed history. This is measured from a baseline window vs a recent "
                f"window, not a single slow run, to reduce false positives from noise."
            ),
            evidence=evidence,
            metrics={
                "key": key,
                "baseline_sample_size": baseline_n,
                "recent_sample_size": recent_n,
                "baseline_median_seconds": round(baseline_median, 1),
                "recent_median_seconds": round(recent_median, 1),
                "pct_increase": round(pct_change, 3),
                "absolute_increase_seconds": round(abs_change, 1),
                "recent_consistency_ratio": round(consistency, 3),
                "linear_trend_slope_seconds_per_run": round(slope, 3) if slope is not None else None,
            },
            severity=severity,
            confidence=confidence,
            recommendation=(
                f"Compare the recent runs against the baseline runs (e.g. "
                f"{baseline_run_ids[-1]} vs {recent_run_ids[0]}) to identify what "
                f"changed - dependency version bumps, new steps, runner changes, larger "
                f"test/data volume, or infrastructure contention are common causes. If "
                f"the increase is intentional (e.g. a new required step was added), this "
                f"finding can be dismissed."
            ),
            estimated_savings_range=savings,
        )

    @staticmethod
    def _severity(pct_change: float) -> Severity:
        if pct_change >= 1.0:
            return Severity.CRITICAL
        if pct_change >= 0.5:
            return Severity.HIGH
        if pct_change >= 0.25:
            return Severity.MEDIUM
        return Severity.LOW

    @staticmethod
    def _confidence(baseline_n: int, recent_n: int, consistency: float) -> Confidence:
        if baseline_n >= 6 and recent_n >= 5 and consistency >= 0.6:
            return Confidence.HIGH
        if baseline_n >= 3 and recent_n >= 3 and consistency >= 0.4:
            return Confidence.MEDIUM
        return Confidence.LOW
