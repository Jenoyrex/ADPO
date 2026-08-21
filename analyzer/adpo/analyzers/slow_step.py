"""
Slow Step Analyzer
===================
Full rationale in REASONING.md ("2. Slow Step Analyzer").

Same statistical approach as the Slow Job Analyzer, but at step
granularity: a step's median duration is compared against its OWN parent
job's median duration (not the whole workflow), which localizes exactly
which step inside a job deserves attention - a slow job is a symptom, a
slow step is (closer to) a cause.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from statistics import median
from typing import Dict, List, Optional, Tuple

from ..models import Confidence, EstimatedSavings, Finding, Severity, WorkflowRun
from ..stats_utils import coefficient_of_variation, percentile
from .base import BaseAnalyzer

StepKey = Tuple[str, str]  # (job_name, step_name)


@dataclass
class SlowStepConfig:
    min_duration_seconds: float = 60.0
    absolute_severe_seconds: float = 300.0
    min_share_of_job: float = 0.4
    min_sample_size: int = 1


class SlowStepAnalyzer(BaseAnalyzer):
    analyzer_type = "slow_step"

    def __init__(self, config: SlowStepConfig = None):
        self.config = config or SlowStepConfig()

    def analyze(self, runs: List[WorkflowRun]) -> List[Finding]:
        findings: List[Finding] = []
        for _workflow_name, wf_runs in self._group_by_workflow(runs).items():
            findings.extend(self._analyze_single_workflow(wf_runs))
        return findings

    def _analyze_single_workflow(self, runs: List[WorkflowRun]) -> List[Finding]:
        cfg = self.config
        step_durations: Dict[StepKey, List[float]] = defaultdict(list)
        step_run_ids: Dict[StepKey, List[str]] = defaultdict(list)
        job_durations: Dict[str, List[float]] = defaultdict(list)

        for run in self._sorted_by_time(runs):
            for job in run.jobs:
                if job.conclusion != "success" or job.duration_seconds is None:
                    continue
                job_durations[job.name].append(job.duration_seconds)
                for step in job.steps:
                    if step.conclusion == "success" and step.duration_seconds is not None:
                        key = (job.name, step.name)
                        step_durations[key].append(step.duration_seconds)
                        step_run_ids[key].append(run.run_id)

        findings: List[Finding] = []
        for (job_name, step_name), durations in step_durations.items():
            n = len(durations)
            if n < cfg.min_sample_size:
                continue
            med = median(durations)
            job_meds = job_durations.get(job_name, [])
            job_med = median(job_meds) if job_meds else None
            share: Optional[float] = (med / job_med) if job_med and job_med > 0 else None

            is_slow_absolute = med >= cfg.absolute_severe_seconds
            is_slow_share = (
                share is not None and med >= cfg.min_duration_seconds and share >= cfg.min_share_of_job
            )
            if not (is_slow_absolute or is_slow_share):
                continue

            p90 = percentile(durations, 90)
            best = min(durations)
            cv = coefficient_of_variation(durations)
            severity = self._severity(med, share, cfg)
            confidence = self._confidence(n)
            savings = self._estimate_savings(med, best, cv, n)

            run_ids = step_run_ids[(job_name, step_name)]
            evidence = [
                f"Step '{step_name}' in job '{job_name}' had a median duration of "
                f"{med:.0f}s across {n} successful run(s).",
            ]
            if share is not None and job_med is not None:
                evidence.append(
                    f"That is {share*100:.0f}% of job '{job_name}''s median total "
                    f"duration ({job_med:.0f}s)."
                )
            evidence.append(
                f"Observed range: {best:.0f}s (fastest) to {max(durations):.0f}s "
                f"(slowest), p90={p90:.0f}s."
            )
            evidence.append(f"Sample run IDs: {', '.join(run_ids[:5])}" + (" ..." if n > 5 else ""))

            findings.append(
                Finding(
                    analyzer_type=self.analyzer_type,
                    title=f"Step '{step_name}' is the dominant time cost in job '{job_name}'",
                    description=(
                        f"The step '{step_name}' within job '{job_name}' takes {med:.0f}s "
                        f"(median), "
                        + (
                            f"which is {share*100:.0f}% of that job's total time."
                            if share is not None
                            else "which exceeds the absolute duration threshold on its own."
                        )
                    ),
                    evidence=evidence,
                    metrics={
                        "job_name": job_name,
                        "step_name": step_name,
                        "sample_size": n,
                        "median_duration_seconds": round(med, 1),
                        "p90_duration_seconds": round(p90, 1),
                        "min_duration_seconds": round(best, 1),
                        "max_duration_seconds": round(max(durations), 1),
                        "coefficient_of_variation": round(cv, 3) if cv is not None else None,
                        "share_of_job_median": round(share, 3) if share is not None else None,
                        "job_median_duration_seconds": round(job_med, 1) if job_med is not None else None,
                    },
                    severity=severity,
                    confidence=confidence,
                    recommendation=self._recommendation(step_name, cv),
                    estimated_savings_range=savings,
                )
            )

        findings.sort(key=lambda f: f.metrics["median_duration_seconds"], reverse=True)
        return findings

    @staticmethod
    def _severity(med: float, share: Optional[float], cfg: SlowStepConfig) -> Severity:
        if med >= cfg.absolute_severe_seconds * 2 or (share is not None and share >= 0.7):
            return Severity.CRITICAL
        if med >= cfg.absolute_severe_seconds or (share is not None and share >= 0.5):
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
                    "This step's duration is consistently high with little variance "
                    "across observed runs, so there is no empirical evidence of a "
                    "faster achievable time from this data alone."
                ),
            )
        gap = med - best
        return EstimatedSavings(
            low_seconds=round(gap * 0.3, 1),
            high_seconds=round(gap, 1),
            unknown=False,
            basis=(
                f"Based on the observed gap between this step's median duration "
                f"({med:.0f}s) and its fastest observed run ({best:.0f}s)."
            ),
        )

    @staticmethod
    def _recommendation(step_name: str, cv) -> str:
        base = f"Profile or break down the '{step_name}' step directly to find its own bottleneck."
        if cv is not None and cv >= 0.25:
            base += (
                " High run-to-run variance suggests network, cache, or runner "
                "contention is a likely factor."
            )
        return base
