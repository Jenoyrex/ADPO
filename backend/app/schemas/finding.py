from __future__ import annotations

from pydantic import BaseModel


class EstimatedSavingsOut(BaseModel):
    low_seconds: float | None
    high_seconds: float | None
    basis: str | None
    unknown: bool


class FindingOut(BaseModel):
    id: int
    analyzer_type: str
    title: str
    description: str
    evidence: list[str]
    metrics: dict
    severity: str
    confidence: str
    recommendation: str
    estimated_savings_range: EstimatedSavingsOut | None

    @classmethod
    def from_model(cls, finding: "app.models.finding.Finding") -> "FindingOut":  # type: ignore[name-defined]
        savings = None
        if not finding.estimated_savings_unknown or finding.estimated_savings_basis:
            savings = EstimatedSavingsOut(
                low_seconds=finding.estimated_savings_low_seconds,
                high_seconds=finding.estimated_savings_high_seconds,
                basis=finding.estimated_savings_basis,
                unknown=finding.estimated_savings_unknown,
            )
        return cls(
            id=finding.id,
            analyzer_type=finding.analyzer_type,
            title=finding.title,
            description=finding.description,
            evidence=list(finding.evidence),
            metrics=dict(finding.metrics),
            severity=finding.severity,
            confidence=finding.confidence,
            recommendation=finding.recommendation,
            estimated_savings_range=savings,
        )


class AnalysisOut(BaseModel):
    id: int
    repository_id: int
    status: str
    runs_analyzed_count: int
    findings: list[FindingOut]
