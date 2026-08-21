"""
Failure / Retry Analyzer
==========================
Full rationale in REASONING.md ("5. Failure/Retry Analyzer").

Two distinct signals, kept separate because they call for different
remediation:

  1. FLAKY / CHRONICALLY FAILING JOBS - based on the observed pass/fail
     rate of each job across distinct workflow runs (using each run's
     *final* attempt as the canonical outcome, so a job that eventually
     passed after a retry is not double-counted as both a failure and a
     success for the same logical run).
  2. RETRY-DRIVEN WASTED COMPUTE TIME - based on runs that required more
     than one attempt; the wasted time is measured directly from the
     duration of the superseded (non-final) attempt(s), not estimated.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List

from ..models import Confidence, EstimatedSavings, Finding, Severity, WorkflowRun
from .base import BaseAnalyzer


@dataclass
class FailureRetryConfig:
    min_sample_size: int = 3
    flaky_min_rate: float = 0.05
    flaky_max_rate: float = 0.85
    chronic_failure_rate: float = 0.85
    min_retry_incidents_for_finding: int = 1


class FailureRetryAnalyzer(BaseAnalyzer):
    analyzer_type = "failure_retry"

    def __init__(self, config: FailureRetryConfig = None):
        self.config = config or FailureRetryConfig()

    def analyze(self, runs: List[WorkflowRun]) -> List[Finding]:
        findings: List[Finding] = []
        for _workflow_name, wf_runs in self._group_by_workflow(runs).items():
            findings.extend(self._analyze_single_workflow(wf_runs))
        return findings

    def _analyze_single_workflow(self, runs: List[WorkflowRun]) -> List[Finding]:
        by_run_number: Dict[int, List[WorkflowRun]] = defaultdict(list)
        for run in runs:
            by_run_number[run.run_number].append(run)
        for group in by_run_number.values():
            group.sort(key=lambda r: r.run_attempt)

        canonical_runs = [group[-1] for group in by_run_number.values()]
        canonical_runs = self._sorted_by_time(canonical_runs)

        findings: List[Finding] = []
        findings.extend(self._flakiness_findings(canonical_runs))
        findings.extend(self._retry_waste_findings(by_run_number))
        return findings

    # ---- flakiness / chronic failure ------------------------------------

    def _flakiness_findings(self, canonical_runs: List[WorkflowRun]) -> List[Finding]:
        cfg = self.config
        stats: Dict[str, Dict[str, int]] = defaultdict(lambda: {"success": 0, "failure": 0, "other": 0})
        failing_run_ids: Dict[str, List[str]] = defaultdict(list)
        durations_on_failure: Dict[str, List[float]] = defaultdict(list)

        for run in canonical_runs:
            for job in run.jobs:
                if job.conclusion == "success":
                    stats[job.name]["success"] += 1
                elif job.conclusion == "failure":
                    stats[job.name]["failure"] += 1
                    failing_run_ids[job.name].append(run.run_id)
                    if job.duration_seconds is not None:
                        durations_on_failure[job.name].append(job.duration_seconds)
                else:
                    stats[job.name]["other"] += 1

        findings: List[Finding] = []
        for job_name, s in stats.items():
            total = s["success"] + s["failure"]
            if total < cfg.min_sample_size or s["failure"] == 0:
                continue
            rate = s["failure"] / total
            if rate < cfg.flaky_min_rate:
                continue

            is_chronic = rate >= cfg.chronic_failure_rate
            classification = "chronic_failure" if is_chronic else "flaky"
            confidence = self._confidence(total)
            severity = self._severity(rate, is_chronic)

            f_run_ids = failing_run_ids[job_name]
            fail_durations = durations_on_failure[job_name]

            evidence = [
                f"Job '{job_name}' failed {s['failure']} of {total} run(s) with a "
                f"conclusive outcome (failure rate {rate*100:.0f}%).",
                f"Example failing run IDs: {', '.join(f_run_ids[:5])}" + (" ..." if len(f_run_ids) > 5 else ""),
            ]
            if fail_durations:
                sorted_fd = sorted(fail_durations)
                evidence.append(
                    f"Failing runs still consumed compute time before failing "
                    f"(median {sorted_fd[len(sorted_fd)//2]:.0f}s)."
                )

            if fail_durations:
                total_wasted = sum(fail_durations)
                avg_wasted = total_wasted / len(fail_durations)
                savings = EstimatedSavings(
                    low_seconds=round(avg_wasted, 1),
                    high_seconds=round(max(fail_durations), 1),
                    unknown=False,
                    basis=(
                        f"Measured compute time consumed by the {len(fail_durations)} "
                        f"observed failing run(s) of this job before they failed - time "
                        f"that would likely be avoided (or at least not repeated) if the "
                        f"failure were fixed. Low end is the average, high end is the "
                        f"single most expensive observed failure."
                    ),
                )
            else:
                savings = EstimatedSavings(
                    low_seconds=None,
                    high_seconds=None,
                    unknown=True,
                    basis="No duration data was available for the observed failing runs.",
                )

            title = (
                f"Job '{job_name}' fails consistently ({rate*100:.0f}% of runs)"
                if is_chronic
                else f"Job '{job_name}' is flaky ({rate*100:.0f}% failure rate)"
            )
            recommendation = (
                "This job fails far more often than it succeeds and does not show the "
                "intermittent success/failure mix typical of flakiness - treat as a "
                "likely genuine, reproducible bug or misconfiguration rather than a "
                "retry/infrastructure problem."
                if is_chronic
                else (
                    "Investigate for non-determinism (test ordering, timing, shared "
                    "state, external service dependencies) before adding retries, since "
                    "retries mask flakiness rather than fix it."
                )
            )

            findings.append(
                Finding(
                    analyzer_type=self.analyzer_type,
                    title=title,
                    description=(
                        f"Across {total} runs with a conclusive outcome, job "
                        f"'{job_name}' failed {s['failure']} time(s)."
                    ),
                    evidence=evidence,
                    metrics={
                        "job_name": job_name,
                        "classification": classification,
                        "total_conclusive_runs": total,
                        "failures": s["failure"],
                        "successes": s["success"],
                        "failure_rate": round(rate, 3),
                    },
                    severity=severity,
                    confidence=confidence,
                    recommendation=recommendation,
                    estimated_savings_range=savings,
                )
            )

        findings.sort(key=lambda f: f.metrics["failure_rate"], reverse=True)
        return findings

    # ---- retry-driven wasted time -----------------------------------------

    def _retry_waste_findings(self, by_run_number: Dict[int, List[WorkflowRun]]) -> List[Finding]:
        cfg = self.config
        wasted_incidents = []  # (run_number, wasted_seconds, non_final_run_ids)

        for run_number, attempts in by_run_number.items():
            if len(attempts) < 2:
                continue
            non_final = attempts[:-1]
            wasted = 0.0
            run_ids = []
            for attempt in non_final:
                for job in attempt.jobs:
                    if job.duration_seconds is not None:
                        wasted += job.duration_seconds
                run_ids.append(attempt.run_id)
            if wasted > 0:
                wasted_incidents.append((run_number, wasted, run_ids))

        if len(wasted_incidents) < cfg.min_retry_incidents_for_finding:
            return []

        total_run_numbers = len(by_run_number)
        retry_rate = len(wasted_incidents) / total_run_numbers if total_run_numbers else 0.0
        wasted_values = [w for _, w, _ in wasted_incidents]
        total_wasted = sum(wasted_values)
        avg_wasted = total_wasted / len(wasted_values)

        confidence = self._retry_confidence(len(wasted_incidents))
        severity = self._retry_severity(retry_rate, avg_wasted)

        example_run_numbers = [str(rn) for rn, _, _ in wasted_incidents[:5]]
        evidence = [
            f"{len(wasted_incidents)} of {total_run_numbers} distinct run(s) "
            f"({retry_rate*100:.0f}%) required more than one attempt to reach a final "
            f"outcome.",
            f"Total measured compute time spent on non-final (superseded) attempts: "
            f"{total_wasted:.0f}s across the observed history (average {avg_wasted:.0f}s "
            f"per retried run).",
            f"Example affected run numbers: {', '.join(example_run_numbers)}"
            + (" ..." if len(wasted_incidents) > 5 else ""),
        ]

        savings = EstimatedSavings(
            low_seconds=round(avg_wasted, 1),
            high_seconds=round(max(wasted_values), 1),
            unknown=False,
            basis=(
                "Directly measured: the sum of job durations recorded on attempt(s) "
                "that were superseded by a later attempt of the same run. This time was "
                "actually consumed and would be avoided if the underlying failure that "
                "triggered the retry were fixed."
            ),
        )

        return [
            Finding(
                analyzer_type=self.analyzer_type,
                title="Re-run/retry attempts are consuming additional CI compute time",
                description=(
                    "Some workflow runs required more than one attempt to complete. "
                    "The time spent on the superseded attempt(s) is measured directly "
                    "from the data, not estimated."
                ),
                evidence=evidence,
                metrics={
                    "retried_run_count": len(wasted_incidents),
                    "total_run_count": total_run_numbers,
                    "retry_rate": round(retry_rate, 3),
                    "total_wasted_seconds": round(total_wasted, 1),
                    "average_wasted_seconds_per_retry": round(avg_wasted, 1),
                    "max_wasted_seconds_single_incident": round(max(wasted_values), 1),
                },
                severity=severity,
                confidence=confidence,
                recommendation=(
                    "Identify which job(s) most often trigger the retry (cross-reference "
                    "with the flaky/chronic-failure findings above) and fix the "
                    "underlying cause. If retries are an intentional flakiness "
                    "mitigation, consider narrowing them to the specific failing job "
                    "rather than the whole workflow."
                ),
                estimated_savings_range=savings,
            )
        ]

    @staticmethod
    def _confidence(total: int) -> Confidence:
        if total >= 15:
            return Confidence.HIGH
        if total >= 6:
            return Confidence.MEDIUM
        return Confidence.LOW

    @staticmethod
    def _severity(rate: float, is_chronic: bool) -> Severity:
        if is_chronic:
            return Severity.CRITICAL if rate >= 0.95 else Severity.HIGH
        if rate >= 0.4:
            return Severity.HIGH
        if rate >= 0.15:
            return Severity.MEDIUM
        return Severity.LOW

    @staticmethod
    def _retry_confidence(n_incidents: int) -> Confidence:
        if n_incidents >= 5:
            return Confidence.HIGH
        if n_incidents >= 2:
            return Confidence.MEDIUM
        return Confidence.LOW

    @staticmethod
    def _retry_severity(retry_rate: float, avg_wasted: float) -> Severity:
        if retry_rate >= 0.3 or avg_wasted >= 600:
            return Severity.HIGH
        if retry_rate >= 0.1 or avg_wasted >= 120:
            return Severity.MEDIUM
        return Severity.LOW
