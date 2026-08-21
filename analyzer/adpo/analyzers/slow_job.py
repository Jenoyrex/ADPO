"""
Slow Job Analyzer
==================
Full rationale in REASONING.md ("1. Slow Job Analyzer").

Purpose
-------
Identify CI jobs that are disproportionately slow, using only durations
actually observed in the run history supplied to the analyzer - no
external benchmarks, no assumed "should take" numbers.

Method (summary)
-----------------
1. Group successful job executions by job name across the supplied run
   history.
2. For each job, compute descriptive statistics (median, p90, min, max,
   coefficient of variation, sample size) of wall-clock duration.
3. Compute each job's median duration as a share of the workflow's own
   median total wall-clock duration ("critical-path share").
4. A job is flagged when EITHER:
     a) its median duration exceeds `min_duration_seconds` AND its
        critical-path share exceeds `min_share_of_workflow`, OR
     b) its median duration exceeds `absolute_severe_seconds` regardless
        of share (a job that is slow in absolute terms is worth surfacing
        even in a workflow with many other slow jobs, where no single job
        would clear a share threshold).
5. Confidence is derived from sample size alone - a statistical fact, not
   a guess.
6. Estimated savings, when offered, are the empirically observed gap
   between the job's median and its own best historically observed
   duration (a real number that happened at least once) - never a
   percentage assumption. If the job shows negligible variance across
   runs, savings are marked `unknown`, because there is no observed
   evidence the job can run any faster; the recommendation instead points
   at the Slow Step Analyzer to localize the cause.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from statistics import median
from typing import Dict, List

from ..models import Confidence, EstimatedSavings, Finding, Severity, WorkflowRun
from ..stats_utils import coefficient_of_variation, percentile
from .base import BaseAnalyzer


@dataclass
class SlowJobConfig:
    min_duration_seconds: float = 180.0       # 3 minutes
    absolute_severe_seconds: float = 600.0    # 10 minutes
    min_share_of_workflow: float = 0.25       # 25% of total pipeline wall time
    min_sample_size: int = 1


class SlowJobAnalyzer(BaseAnalyzer):
    analyzer_type = "slow_job"

    def __init__(self, config: SlowJobConfig = None):
        self.config = config or SlowJobConfig()

    def analyze(self, runs: List[WorkflowRun]) -> List[Finding]:
        findings: List[Finding] = []
        for _workflow_name, wf_runs in self._group_by_workflow(runs).items():
            findings.extend(self._analyze_single_workflow(wf_runs))
        return findings

    def _analyze_single_workflow(self, runs: List[WorkflowRun]) -> List[Finding]:
        cfg = self.config
        findings: List[Finding] = []

        job_durations: Dict[str, List[float]] = defaultdict(list)
        job_run_ids: Dict[str, List[str]] = defaultdict(list)
        workflow_durations: List[float] = []

        for run in self._sorted_by_time(runs):
            wf_dur = run.duration_seconds
            if run.conclusion == "success" and wf_dur is not None:
                workflow_durations.append(wf_dur)
            for job in run.jobs:
                if job.conclusion == "success" and job.duration_seconds is not None:
                    job_durations[job.name].append(job.duration_seconds)
                    job_run_ids[job.name].append(run.run_id)

        if not workflow_durations:
            return findings

        workflow_median = median(workflow_durations)

        for job_name, durations in job_durations.items():
            n = len(durations)
            if n < cfg.min_sample_size:
                continue
            med = median(durations)
            share = med / workflow_median if workflow_median > 0 else 0.0

            is_slow_by_share = med >= cfg.min_duration_seconds and share >= cfg.min_share_of_workflow
            is_slow_absolute = med >= cfg.absolute_severe_seconds
            if not (is_slow_by_share or is_slow_absolute):
                continue

            p90 = percentile(durations, 90)
            best = min(durations)
            cv = coefficient_of_variation(durations)

            severity = self._severity(med, share, cfg)
            confidence = self._confidence(n)
            savings = self._estimate_savings(med, best, cv, n)

            run_ids = job_run_ids[job_name]
            evidence = [
                f"Job '{job_name}' had a median duration of {med:.0f}s across {n} "
                f"successful run(s).",
                f"This represents {share*100:.0f}% of the workflow's median total "
                f"duration ({workflow_median:.0f}s).",
                f"Observed range: {best:.0f}s (fastest) to {max(durations):.0f}s "
                f"(slowest), p90={p90:.0f}s.",
                f"Sample run IDs: {', '.join(run_ids[:5])}" + (" ..." if n > 5 else ""),
            ]

            findings.append(
                Finding(
                    analyzer_type=self.analyzer_type,
                    title=f"Job '{job_name}' is a significant contributor to pipeline duration",
                    description=(
                        f"The job '{job_name}' consistently takes a large amount of "
                        f"wall-clock time ({med:.0f}s median) relative to "
                        + ("the rest of the workflow." if is_slow_by_share
                           else "an absolute duration threshold.")
                    ),
                    evidence=evidence,
                    metrics={
                        "job_name": job_name,
                        "sample_size": n,
                        "median_duration_seconds": round(med, 1),
                        "p90_duration_seconds": round(p90, 1),
                        "min_duration_seconds": round(best, 1),
                        "max_duration_seconds": round(max(durations), 1),
                        "coefficient_of_variation": round(cv, 3) if cv is not None else None,
                        "share_of_workflow_median": round(share, 3),
                        "workflow_median_duration_seconds": round(workflow_median, 1),
                    },
                    severity=severity,
                    confidence=confidence,
                    recommendation=self._recommendation(cv, share),
                    estimated_savings_range=savings,
                )
            )

        findings.sort(key=lambda f: f.metrics["median_duration_seconds"], reverse=True)
        return findings

    @staticmethod
    def _severity(med: float, share: float, cfg: SlowJobConfig) -> Severity:
        if med >= cfg.absolute_severe_seconds * 2 or share >= 0.5:
            return Severity.CRITICAL
        if med >= cfg.absolute_severe_seconds or share >= 0.35:
            return Severity.HIGH
        if med >= cfg.min_duration_seconds:
            return Severity.MEDIUM
        return Severity.LOW

    @staticmethod
    def _confidence(n: int) -> Confidence:
        if n >= 10:
            return Confidence.HIGH
        if n >= 4:
            return Confidence.MEDIUM
        return Confidence.LOW

    @staticmethod
    def _estimate_savings(med: float, best: float, cv, n: int) -> EstimatedSavings:
        if n < 3 or cv is None or cv < 0.10:
            return EstimatedSavings(
                low_seconds=None,
                high_seconds=None,
                unknown=True,
                basis=(
                    "Duration is consistently high across observed runs with little "
                    "variance, so there is no empirical evidence of a faster achievable "
                    "time. Root-cause investigation (e.g. step-level analysis) is needed "
                    "before a savings estimate can be made."
                ),
            )
        gap = med - best
        return EstimatedSavings(
            low_seconds=round(gap * 0.3, 1),
            high_seconds=round(gap, 1),
            unknown=False,
            basis=(
                f"Based on the observed gap between this job's median duration "
                f"({med:.0f}s) and its fastest observed run ({best:.0f}s) in the "
                f"supplied history. The low end assumes only partial recovery of that "
                f"gap; the high end assumes the job could reliably match its own best "
                f"observed performance."
            ),
        )

    @staticmethod
    def _recommendation(cv, share: float) -> str:
        base = (
            "Investigate this job's steps to identify the largest time contributors "
            "(see the Slow Step Analyzer)."
        )
        if cv is not None and cv >= 0.25:
            base += (
                " High run-to-run variance suggests network, cache, or runner "
                "contention issues worth investigating first."
            )
        if share >= 0.4:
            base += (
                " Because this job dominates total pipeline time, it is a good "
                "candidate for splitting into parallel jobs if its work is independent "
                "(verify with the Parallelization Analyzer's cautions in mind)."
            )
        return base
