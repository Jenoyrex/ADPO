"""
ADPO - deterministic, evidence-based CI/CD (GitHub Actions) analysis engine.

No LLM is used anywhere in this package. Every analyzer computes its
findings from structured, statistical analysis of `WorkflowRun` history.
"""
from . import ingest
from .engine import AnalysisEngine, default_analyzers
from .models import Confidence, EstimatedSavings, Finding, Job, Severity, Step, WorkflowRun

__all__ = [
    "Step",
    "Job",
    "WorkflowRun",
    "Finding",
    "Severity",
    "Confidence",
    "EstimatedSavings",
    "AnalysisEngine",
    "default_analyzers",
    "ingest",
]
