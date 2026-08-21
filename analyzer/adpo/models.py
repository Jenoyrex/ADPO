"""
Core data models for the ADPO deterministic analysis engine.

These models are the shared contract between:
  * `adpo.ingest`      - turns raw GitHub-Actions-shaped JSON into these objects
  * `adpo.analyzers.*` - read these objects and never anything else (no network
                         calls, no LLMs, no hidden state)
  * `adpo.engine`      - orchestrates analyzers and collects their output

Keeping the model intentionally small and dependency-free (stdlib only)
means every analyzer is testable by constructing these dataclasses
directly, with no need for a real GitHub API connection or fixtures
files on disk.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class Severity(str, Enum):
    """Impact magnitude of a finding, not a measure of certainty."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Confidence(str, Enum):
    """How statistically reliable the finding is, given the data supplied.

    This is deliberately independent from `Severity`: a finding can have
    huge potential impact (HIGH severity) but be based on only a couple of
    data points (LOW confidence), or vice versa.
    """
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class Step:
    """A single step within a job, mirroring the GitHub Actions job-steps shape."""

    name: str
    number: int
    status: str  # "completed", "in_progress", "queued"
    conclusion: Optional[str]  # "success", "failure", "cancelled", "skipped", None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    uses: Optional[str] = None  # e.g. "actions/setup-node@v4" (None for `run:` steps)
    with_params: Dict[str, Any] = field(default_factory=dict)  # action inputs, e.g. {"cache": "npm"}
    run: Optional[str] = None  # raw shell command, for `run:` steps

    @property
    def duration_seconds(self) -> Optional[float]:
        if self.started_at is None or self.completed_at is None:
            return None
        delta = (self.completed_at - self.started_at).total_seconds()
        return delta if delta >= 0 else None


@dataclass
class Job:
    """A single job within a workflow run, mirroring the GitHub Actions job shape.

    `needs` is not part of the raw GitHub "list jobs" API response (it lives
    in the workflow YAML) but ADPO's ingestion layer is expected to enrich
    each job with its declared `needs` list, since several analyzers
    (notably the parallelization analyzer) require it.
    """

    job_id: str
    name: str
    status: str
    conclusion: Optional[str]
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    steps: List[Step] = field(default_factory=list)
    needs: List[str] = field(default_factory=list)
    runs_on: Optional[str] = None
    run_attempt: int = 1

    @property
    def duration_seconds(self) -> Optional[float]:
        if self.started_at is None or self.completed_at is None:
            return None
        delta = (self.completed_at - self.started_at).total_seconds()
        return delta if delta >= 0 else None


@dataclass
class WorkflowRun:
    """A single attempt of a single workflow run."""

    run_id: str
    workflow_name: str
    run_number: int
    branch: str
    event: str
    created_at: datetime
    conclusion: Optional[str]
    jobs: List[Job] = field(default_factory=list)
    run_attempt: int = 1
    updated_at: Optional[datetime] = None

    @property
    def duration_seconds(self) -> Optional[float]:
        starts = [j.started_at for j in self.jobs if j.started_at is not None]
        ends = [j.completed_at for j in self.jobs if j.completed_at is not None]
        if not starts or not ends:
            return None
        delta = (max(ends) - min(starts)).total_seconds()
        return delta if delta >= 0 else None

    def job_by_name(self, name: str) -> Optional[Job]:
        for j in self.jobs:
            if j.name == name:
                return j
        return None


@dataclass
class EstimatedSavings:
    """A defensible, data-derived savings estimate - or an explicit "unknown".

    ADPO never invents a savings number. Every non-unknown instance of this
    class must be traceable, via `basis`, to values actually observed in
    the supplied run history (e.g. "gap between median and best-observed
    duration", "measured wasted retry time"). When the data does not
    support an estimate, `unknown=True` and both bounds are None.
    """

    low_seconds: Optional[float]
    high_seconds: Optional[float]
    basis: str
    unknown: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "low_seconds": self.low_seconds,
            "high_seconds": self.high_seconds,
            "unknown": self.unknown,
            "basis": self.basis,
        }


@dataclass
class Finding:
    """The uniform output shape every analyzer must produce."""

    analyzer_type: str
    title: str
    description: str
    evidence: List[str]
    metrics: Dict[str, Any]
    severity: Severity
    confidence: Confidence
    recommendation: str
    estimated_savings_range: Optional[EstimatedSavings] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "analyzer_type": self.analyzer_type,
            "title": self.title,
            "description": self.description,
            "evidence": list(self.evidence),
            "metrics": dict(self.metrics),
            "severity": self.severity.value if isinstance(self.severity, Severity) else self.severity,
            "confidence": self.confidence.value if isinstance(self.confidence, Confidence) else self.confidence,
            "recommendation": self.recommendation,
            "estimated_savings_range": (
                self.estimated_savings_range.to_dict() if self.estimated_savings_range else None
            ),
        }
