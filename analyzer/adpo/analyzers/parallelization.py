"""
Potential Parallelization Analyzer
=====================================
Full rationale in REASONING.md ("6. Potential Parallelization Analyzer").

THIS ANALYZER NEVER CLAIMS A PAIR OF JOBS IS SAFE TO PARALLELIZE.

It only surfaces pairs of jobs that:
  (a) have no declared `needs` dependency between them, directly or
      transitively, in the CURRENT workflow definition, AND
  (b) have nonetheless been observed to run one after the other, with no
      time overlap, consistently across the supplied run history.

That pattern is a *candidate worth a human looking at* - it is equally
consistent with:
  * a genuine but UNDECLARED dependency (job B silently reads an
    artifact/cache/file/external state produced by job A without a
    `needs` edge - which would actually be a latent correctness bug worth
    fixing regardless of any speed benefit),
  * limited concurrent runner capacity forcing serialization even though
    the jobs are logically independent, or
  * coincidental scheduling.

None of these can be distinguished from run metadata alone, so:
  * `confidence` is capped at MEDIUM (see `_confidence` docstring) and
    reflects only how statistically reliable the *pattern* is, never
    whether parallelization is actually safe;
  * `severity` is capped at MEDIUM (this is an opportunity to *investigate*,
    not a problem);
  * the finding's `description` and `recommendation` explicitly state that
    this is not a safety determination and list concrete things a human
    must verify first;
  * the low end of `estimated_savings_range` is always 0, because the
    true achievable savings may genuinely be zero if parallelization
    turns out to be unsafe.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations
from statistics import median
from typing import Dict, List, Optional, Set

from ..models import Confidence, EstimatedSavings, Finding, Severity, WorkflowRun
from .base import BaseAnalyzer


@dataclass
class ParallelizationConfig:
    min_run_samples: int = 2
    min_job_duration_seconds: float = 20.0
    min_sequential_consistency: float = 0.75
    overlap_epsilon_seconds: float = 1.0


class ParallelizationAnalyzer(BaseAnalyzer):
    analyzer_type = "potential_parallelization"

    def __init__(self, config: ParallelizationConfig = None):
        self.config = config or ParallelizationConfig()

    def analyze(self, runs: List[WorkflowRun]) -> List[Finding]:
        cfg = self.config
        by_workflow: Dict[str, List[WorkflowRun]] = defaultdict(list)
        for run in runs:
            by_workflow[run.workflow_name].append(run)

        findings: List[Finding] = []
        for workflow_name, wf_runs in by_workflow.items():
            wf_runs_sorted = self._sorted_by_time(wf_runs)
            if not wf_runs_sorted:
                continue
            latest = wf_runs_sorted[-1]
            job_names = [j.name for j in latest.jobs]
            needs_map = {j.name: set(j.needs) for j in latest.jobs}
            closure = self._transitive_closure(needs_map)

            for a_name, b_name in combinations(sorted(job_names), 2):
                if self._related(a_name, b_name, closure):
                    continue  # a declared dependency exists - never a parallelization candidate
                finding = self._evaluate_pair(workflow_name, a_name, b_name, wf_runs_sorted, cfg)
                if finding:
                    findings.append(finding)

        findings.sort(
            key=lambda f: f.metrics.get("theoretical_best_case_savings_seconds", 0), reverse=True
        )
        return findings

    @staticmethod
    def _transitive_closure(needs_map: Dict[str, Set[str]]) -> Dict[str, Set[str]]:
        closure: Dict[str, Set[str]] = {}
        for name in needs_map:
            seen: Set[str] = set()
            stack = list(needs_map.get(name, set()))
            while stack:
                cur = stack.pop()
                if cur in seen or cur == name:
                    continue
                seen.add(cur)
                stack.extend(needs_map.get(cur, set()))
            closure[name] = seen
        return closure

    @staticmethod
    def _related(a: str, b: str, closure: Dict[str, Set[str]]) -> bool:
        return b in closure.get(a, set()) or a in closure.get(b, set())

    def _evaluate_pair(
        self,
        workflow_name: str,
        a_name: str,
        b_name: str,
        wf_runs: List[WorkflowRun],
        cfg: ParallelizationConfig,
    ) -> Optional[Finding]:
        a_durations: List[float] = []
        b_durations: List[float] = []
        checked = 0
        sequential_count = 0
        a_first_count = 0
        sample_run_ids: List[str] = []

        for run in wf_runs:
            job_a = run.job_by_name(a_name)
            job_b = run.job_by_name(b_name)
            if not job_a or not job_b:
                continue
            if job_a.conclusion != "success" or job_b.conclusion != "success":
                continue
            if None in (job_a.started_at, job_a.completed_at, job_b.started_at, job_b.completed_at):
                continue

            checked += 1
            a_durations.append(job_a.duration_seconds)
            b_durations.append(job_b.duration_seconds)

            overlap = min(job_a.completed_at, job_b.completed_at) - max(job_a.started_at, job_b.started_at)
            overlap_seconds = overlap.total_seconds()

            if overlap_seconds <= cfg.overlap_epsilon_seconds:
                sequential_count += 1
                if job_a.completed_at <= job_b.started_at:
                    a_first_count += 1
                sample_run_ids.append(run.run_id)

        if checked < cfg.min_run_samples:
            return None

        consistency = sequential_count / checked
        if consistency < cfg.min_sequential_consistency:
            return None

        a_med = median(a_durations)
        b_med = median(b_durations)
        if a_med < cfg.min_job_duration_seconds or b_med < cfg.min_job_duration_seconds:
            return None

        b_first_count = sequential_count - a_first_count
        order_consistency = (max(a_first_count, b_first_count) / sequential_count) if sequential_count else 0.0
        usual_first, usual_second = (a_name, b_name) if a_first_count >= b_first_count else (b_name, a_name)

        confidence = self._confidence(checked, consistency)
        theoretical_best_case = min(a_med, b_med)
        severity = self._severity(theoretical_best_case)

        evidence = [
            f"Jobs '{a_name}' and '{b_name}' have no declared `needs` relationship "
            f"(direct or transitive) in the current definition of workflow "
            f"'{workflow_name}'.",
            f"Across {checked} run(s) where both jobs completed successfully with "
            f"valid timing data, they did not overlap in execution in "
            f"{sequential_count} ({consistency*100:.0f}%) of them.",
            f"When sequential, '{usual_first}' ran before '{usual_second}' in "
            f"{max(a_first_count, b_first_count)}/{sequential_count} instance(s) "
            f"(order consistency {order_consistency*100:.0f}%).",
            f"Median durations: '{a_name}'={a_med:.0f}s, '{b_name}'={b_med:.0f}s.",
            f"Example run IDs: {', '.join(sample_run_ids[:5])}" + (" ..." if len(sample_run_ids) > 5 else ""),
        ]

        savings = EstimatedSavings(
            low_seconds=0.0,
            high_seconds=round(theoretical_best_case, 1),
            unknown=False,
            basis=(
                f"Theoretical upper bound ONLY, contingent on verification: if these "
                f"jobs were run fully in parallel with neither waiting on the other, "
                f"the critical path could shrink by up to min(median_A, median_B) = "
                f"{theoretical_best_case:.0f}s per run. The low end is 0s because it is "
                f"entirely possible these jobs cannot safely be parallelized at all "
                f"(e.g. an undeclared shared-state dependency), in which case the true "
                f"achievable savings is zero."
            ),
        )

        return Finding(
            analyzer_type=self.analyzer_type,
            title=f"'{a_name}' and '{b_name}' run sequentially with no declared dependency between them",
            description=(
                f"'{a_name}' and '{b_name}' are not linked by a `needs` relationship, "
                f"yet across the observed history they consistently ran one after the "
                f"other rather than concurrently. THIS FINDING DOES NOT ESTABLISH THAT "
                f"RUNNING THEM CONCURRENTLY WOULD BE SAFE - it only means the pattern is "
                f"worth a human looking at. Common explanations include: (1) a genuine but undeclared "
                f"dependency - one job may read an artifact, cache entry, file, or "
                f"external state produced by the other without a `needs` edge, which "
                f"would itself be a latent correctness issue worth fixing regardless of "
                f"speed; (2) limited concurrent runner capacity forcing serialization "
                f"even though the jobs are independent; or (3) coincidental scheduling."
            ),
            evidence=evidence,
            metrics={
                "workflow_name": workflow_name,
                "job_a": a_name,
                "job_b": b_name,
                "sample_size": checked,
                "sequential_count": sequential_count,
                "sequential_consistency": round(consistency, 3),
                "order_consistency": round(order_consistency, 3),
                "usual_order": f"{usual_first} -> {usual_second}",
                "job_a_median_seconds": round(a_med, 1),
                "job_b_median_seconds": round(b_med, 1),
                "theoretical_best_case_savings_seconds": round(theoretical_best_case, 1),
            },
            severity=severity,
            confidence=confidence,
            recommendation=(
                "Before making any change: (1) confirm neither job consumes an "
                "artifact, cache entry, file, or environment state produced by the "
                "other without a declared `needs` edge; (2) confirm the runner pool has "
                "capacity to run both concurrently without queuing delays that would "
                "erase the benefit; (3) only then consider restructuring `needs` so the "
                "scheduler is free to run them concurrently. Do not remove or infer "
                "dependency edges based on this finding alone."
            ),
            estimated_savings_range=savings,
        )

    @staticmethod
    def _confidence(checked: int, consistency: float) -> Confidence:
        """Confidence in the *pattern* only - deliberately never HIGH.

        HIGH confidence would read as "confident this is a real
        opportunity", which this analyzer can never establish from run
        metadata alone (see module docstring). It can only be confident
        that jobs reliably don't overlap and have no declared edge.
        """
        if checked >= 4 and consistency >= 0.9:
            return Confidence.MEDIUM
        return Confidence.LOW

    @staticmethod
    def _severity(theoretical_best_case: float) -> Severity:
        # Capped at MEDIUM: this is an investigation opportunity, not a
        # confirmed problem, so it should never compete with HIGH/CRITICAL
        # findings from analyzers that measure an actual, uncontested cost.
        if theoretical_best_case >= 180:
            return Severity.MEDIUM
        return Severity.LOW
