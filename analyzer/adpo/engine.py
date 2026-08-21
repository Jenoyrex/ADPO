"""
AnalysisEngine: orchestrates the deterministic analyzers over a shared run
history and collects their Findings.

This is intentionally a thin layer - all analytical logic lives in the
individual analyzer modules so each can be developed, tested, and reasoned
about independently. The engine's only jobs are: run every analyzer over
the same input, isolate any single analyzer's failure from the rest, and
hand back a flat list of Findings (or dicts, for easy JSON serialization).
"""
from __future__ import annotations

from typing import List, Optional

from .analyzers import (
    BaseAnalyzer,
    DependencyInstallAnalyzer,
    FailureRetryAnalyzer,
    HistoricalRegressionAnalyzer,
    ParallelizationAnalyzer,
    SlowJobAnalyzer,
    SlowStepAnalyzer,
)
from .models import Finding, WorkflowRun


def default_analyzers() -> List[BaseAnalyzer]:
    return [
        SlowJobAnalyzer(),
        SlowStepAnalyzer(),
        HistoricalRegressionAnalyzer(),
        DependencyInstallAnalyzer(),
        FailureRetryAnalyzer(),
        ParallelizationAnalyzer(),
    ]


class AnalysisEngine:
    def __init__(self, analyzers: Optional[List[BaseAnalyzer]] = None, strict: bool = False):
        self.analyzers = analyzers if analyzers is not None else default_analyzers()
        self.strict = strict
        self.errors: List[str] = []

    def run(self, runs: List[WorkflowRun]) -> List[Finding]:
        self.errors = []
        all_findings: List[Finding] = []
        for analyzer in self.analyzers:
            try:
                all_findings.extend(analyzer.analyze(runs))
            except Exception as exc:  # noqa: BLE001 - isolate one bad analyzer from the rest
                if self.strict:
                    raise
                self.errors.append(f"{analyzer.analyzer_type}: {exc!r}")
        return all_findings

    def run_as_dicts(self, runs: List[WorkflowRun]) -> List[dict]:
        return [f.to_dict() for f in self.run(runs)]
