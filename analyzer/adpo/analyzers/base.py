"""Common contract shared by every deterministic analyzer."""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Dict, List

from ..models import Finding, WorkflowRun


class BaseAnalyzer(ABC):
    """
    Each analyzer:
      * takes a run history - which MAY span multiple distinct workflows
        (e.g. `CI.yml` and `Release.yml` in the same repo) - and returns
        zero or more `Finding`s. Analyzers are responsible for internally
        scoping their statistics to one workflow at a time (see
        `_group_by_workflow`) so that unrelated pipelines' durations,
        job names, and dependency graphs are never blended together;
      * must never raise on empty input (`analyze([])` must return `[]`);
      * must never fabricate numbers - every number that appears in a
        `Finding` must be traceable to values present in the input
        `WorkflowRun` objects (durations, counts, timestamps);
      * must be independently importable and testable with no dependency
        on any other analyzer or on I/O.
    """

    analyzer_type: str = "base"

    @abstractmethod
    def analyze(self, runs: List[WorkflowRun]) -> List[Finding]:
        raise NotImplementedError

    @staticmethod
    def _sorted_by_time(runs: List[WorkflowRun]) -> List[WorkflowRun]:
        return sorted(runs, key=lambda r: r.created_at)

    @staticmethod
    def _group_by_workflow(runs: List[WorkflowRun]) -> Dict[str, List[WorkflowRun]]:
        """Split a (possibly multi-workflow) run history into per-workflow
        buckets. Every analyzer's core logic should be applied once per
        bucket, never across buckets, so that e.g. a job named "build" in
        one workflow is never averaged together with an unrelated job
        also named "build" in a different workflow."""
        grouped: Dict[str, List[WorkflowRun]] = defaultdict(list)
        for run in runs:
            grouped[run.workflow_name].append(run)
        return grouped
