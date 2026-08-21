"""
Dependency Installation Analyzer
==================================
Full rationale in REASONING.md ("4. Dependency Installation Analyzer").

Flags CI steps that look like package/dependency installation - matched
via a keyword list against the step's shell command and/or step name,
plain deterministic pattern matching, no LLM and no execution of the
command - which are either:

  (a) not preceded by any recognizable caching mechanism in the same job, or
  (b) preceded by a caching mechanism but still show meaningful
      run-to-run duration variance, which is a *signal* (not proof) that
      the cache may not be hitting reliably.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from statistics import median
from typing import Dict, List, Tuple

from ..models import Confidence, EstimatedSavings, Finding, Severity, Step, WorkflowRun
from ..stats_utils import coefficient_of_variation, percentile
from .base import BaseAnalyzer

InstallKey = Tuple[str, str]  # (job_name, step_name)

INSTALL_KEYWORDS = [
    "npm install", "npm ci", "yarn install", "yarn add", "pnpm install",
    "pip install", "pip3 install", "poetry install", "pipenv install",
    "bundle install", "gem install",
    "go mod download", "go get ",
    "mvn install", "mvn dependency", "mvn -b install",
    "gradle build", "gradle dependencies", "./gradlew build",
    "apt-get install", "apt install", "yum install", "apk add",
    "composer install",
    "cargo build", "cargo fetch",
    "pod install",
]

CACHE_AWARE_SETUP_ACTIONS = {
    "actions/setup-node", "actions/setup-python", "actions/setup-java",
    "actions/setup-go", "actions/setup-dotnet", "ruby/setup-ruby",
}


def _is_install_step(step: Step) -> bool:
    haystack = f"{step.run or ''} {step.name or ''}".lower()
    return any(kw in haystack for kw in INSTALL_KEYWORDS)


def _step_has_cache_action(step: Step) -> bool:
    if not step.uses:
        return False
    action = step.uses.split("@")[0].strip().lower()
    if action == "actions/cache":
        return True
    if action in CACHE_AWARE_SETUP_ACTIONS:
        cache_param = step.with_params.get("cache") if step.with_params else None
        return bool(cache_param) and str(cache_param).strip().lower() not in ("false", "", "none")
    return False


@dataclass
class DependencyInstallConfig:
    min_duration_seconds: float = 20.0
    ineffective_cache_cv_threshold: float = 0.30
    savings_meaningful_cv_threshold: float = 0.10
    min_sample_size: int = 1


class DependencyInstallAnalyzer(BaseAnalyzer):
    analyzer_type = "dependency_install"

    def __init__(self, config: DependencyInstallConfig = None):
        self.config = config or DependencyInstallConfig()

    def analyze(self, runs: List[WorkflowRun]) -> List[Finding]:
        findings: List[Finding] = []
        for _workflow_name, wf_runs in self._group_by_workflow(runs).items():
            findings.extend(self._analyze_single_workflow(wf_runs))
        return findings

    def _analyze_single_workflow(self, runs: List[WorkflowRun]) -> List[Finding]:
        cfg = self.config
        groups: Dict[InstallKey, List[dict]] = defaultdict(list)

        for run in self._sorted_by_time(runs):
            for job in run.jobs:
                cache_seen = False
                for step in job.steps:
                    if _step_has_cache_action(step):
                        cache_seen = True
                        continue
                    if (
                        step.conclusion == "success"
                        and step.duration_seconds is not None
                        and _is_install_step(step)
                    ):
                        key = (job.name, step.name)
                        groups[key].append(
                            {
                                "duration": step.duration_seconds,
                                "run_id": run.run_id,
                                "cached": cache_seen,
                                "command": (step.run or step.name or "")[:80],
                            }
                        )

        findings: List[Finding] = []
        for (job_name, step_name), entries in groups.items():
            n = len(entries)
            if n < cfg.min_sample_size:
                continue
            durations = [e["duration"] for e in entries]
            med = median(durations)
            if med < cfg.min_duration_seconds:
                continue

            currently_cached = entries[-1]["cached"]
            cache_ever_seen = any(e["cached"] for e in entries)
            cv = coefficient_of_variation(durations)

            if currently_cached and (cv is None or cv < cfg.ineffective_cache_cv_threshold):
                continue  # cached and stable/fast enough - not worth flagging

            duplicate_job_count = self._duplicate_job_count(step_name, groups)

            if not currently_cached:
                classification = "uncached"
                title = f"Dependency installation in '{job_name}' / '{step_name}' has no detected caching"
            else:
                classification = "cache_possibly_ineffective"
                title = f"Cached dependency installation in '{job_name}' / '{step_name}' still shows high variance"

            severity = self._severity(med, classification)
            confidence = self._confidence(n, classification)
            savings = self._estimate_savings(med, durations, cv, cfg)

            sample_cmd = entries[-1]["command"]
            evidence = [
                f"Step '{step_name}' in job '{job_name}' matched dependency-installation "
                f'keyword patterns (e.g. command/name: "{sample_cmd}").',
                f"Median duration across {n} observed successful run(s): {med:.0f}s "
                f"(min {min(durations):.0f}s, max {max(durations):.0f}s).",
                (
                    "No caching action (e.g. actions/cache, or a setup-* action with "
                    "cache enabled) was detected before this step in the most recent run."
                    if not currently_cached
                    else (
                        f"A caching action was detected, but duration still varies "
                        f"(coefficient of variation={cv:.2f}) across runs, which can "
                        f"indicate inconsistent cache hits."
                    )
                ),
            ]
            if duplicate_job_count > 1:
                evidence.append(
                    f"The same install step name occurs independently in "
                    f"{duplicate_job_count} different jobs, so this cost is likely paid "
                    f"multiple times per run."
                )

            findings.append(
                Finding(
                    analyzer_type=self.analyzer_type,
                    title=title,
                    description=(
                        "Dependency installation is a common, highly cacheable source of "
                        "CI time. This step was matched via command/name pattern "
                        "matching, not by executing the command."
                    ),
                    evidence=evidence,
                    metrics={
                        "job_name": job_name,
                        "step_name": step_name,
                        "sample_size": n,
                        "median_duration_seconds": round(med, 1),
                        "min_duration_seconds": round(min(durations), 1),
                        "max_duration_seconds": round(max(durations), 1),
                        "coefficient_of_variation": round(cv, 3) if cv is not None else None,
                        "cache_action_detected": currently_cached,
                        "cache_action_seen_in_any_run": cache_ever_seen,
                        "classification": classification,
                        "duplicate_job_occurrences": duplicate_job_count,
                    },
                    severity=severity,
                    confidence=confidence,
                    recommendation=self._recommendation(classification, duplicate_job_count),
                    estimated_savings_range=savings,
                )
            )

        findings.sort(key=lambda f: f.metrics["median_duration_seconds"], reverse=True)
        return findings

    @staticmethod
    def _duplicate_job_count(step_name: str, groups: Dict[InstallKey, List[dict]]) -> int:
        jobs_with_step = {jn for (jn, sn) in groups.keys() if sn == step_name}
        return len(jobs_with_step)

    @staticmethod
    def _severity(med: float, classification: str) -> Severity:
        if classification == "uncached":
            if med >= 300:
                return Severity.HIGH
            if med >= 60:
                return Severity.MEDIUM
            return Severity.LOW
        if med >= 300:
            return Severity.MEDIUM
        return Severity.LOW

    @staticmethod
    def _confidence(n: int, classification: str) -> Confidence:
        base = Confidence.HIGH if n >= 8 else (Confidence.MEDIUM if n >= 3 else Confidence.LOW)
        if classification == "cache_possibly_ineffective":
            # We can never directly observe a cache hit/miss from run metadata
            # alone, so this classification is capped at MEDIUM confidence.
            return Confidence.MEDIUM if base == Confidence.HIGH else base
        return base

    @staticmethod
    def _estimate_savings(med: float, durations: List[float], cv, cfg: DependencyInstallConfig) -> EstimatedSavings:
        n = len(durations)
        if n < 3 or cv is None or cv < cfg.savings_meaningful_cv_threshold:
            return EstimatedSavings(
                low_seconds=None,
                high_seconds=None,
                unknown=True,
                basis=(
                    "Duration is consistently high with little run-to-run variance, so "
                    "there is no observed fast run to anchor a savings estimate. Caching "
                    "would still be expected to help in principle, but the magnitude "
                    "cannot be estimated from this data alone."
                ),
            )
        best = min(durations)
        p25 = percentile(durations, 25)
        gap_conservative = max(0.0, med - p25)
        gap_optimistic = max(0.0, med - best)
        return EstimatedSavings(
            low_seconds=round(gap_conservative, 1),
            high_seconds=round(gap_optimistic, 1),
            unknown=False,
            basis=(
                f"Derived from this step's own observed duration spread: p25={p25:.0f}s, "
                f"min={best:.0f}s, median={med:.0f}s. Low end assumes only modest "
                f"improvement, high end assumes best-case-observed improvement."
            ),
        )

    @staticmethod
    def _recommendation(classification: str, duplicate_job_count: int) -> str:
        if classification == "uncached":
            msg = (
                "Add a caching step (e.g. actions/cache, or the built-in cache option "
                "on the relevant actions/setup-* action) keyed on the lockfile hash, "
                "before this install step."
            )
        else:
            msg = (
                "Verify the cache key actually changes only when dependencies change "
                "(an overly broad or overly narrow key both hurt hit rate), and confirm "
                "cache hits in the step logs."
            )
        if duplicate_job_count > 1:
            msg += (
                " Since this install runs independently in multiple jobs, a "
                "shared/reused cache key across those jobs would compound the benefit."
            )
        return msg
