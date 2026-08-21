"""Runs the locked `adpo.AnalysisEngine` over a repository's stored run
history and persists the results. No analysis logic lives here - this is
orchestration + persistence only."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from adpo.engine import AnalysisEngine

from app.core.logging import get_logger, log_extra
from app.models.analysis import Analysis
from app.models.finding import Finding
from app.models.job import Job
from app.models.workflow_run import WorkflowRun
from app.services.analysis.adapter import workflow_runs_to_adpo

logger = get_logger("adpo.analysis")


def run_analysis(db: Session, repository_id: int) -> Analysis:
    started_at = datetime.now(timezone.utc)
    analysis = Analysis(repository_id=repository_id, status="pending", started_at=started_at)
    db.add(analysis)
    db.flush()  # assign analysis.id without committing yet

    runs = (
        db.execute(
            select(WorkflowRun)
            .where(WorkflowRun.repository_id == repository_id)
            .options(
                selectinload(WorkflowRun.workflow),
                selectinload(WorkflowRun.jobs).selectinload(Job.steps),
            )
        )
        .scalars()
        .all()
    )

    try:
        adpo_runs = workflow_runs_to_adpo(list(runs))
        engine = AnalysisEngine()  # runs all default analyzers; strict=False isolates a bad analyzer
        findings = engine.run(adpo_runs)

        for finding in findings:
            savings = finding.estimated_savings_range
            db.add(
                Finding(
                    analysis_id=analysis.id,
                    analyzer_type=finding.analyzer_type,
                    title=finding.title,
                    description=finding.description,
                    evidence=list(finding.evidence),
                    metrics=dict(finding.metrics),
                    severity=finding.severity.value,
                    confidence=finding.confidence.value,
                    recommendation=finding.recommendation,
                    estimated_savings_low_seconds=savings.low_seconds if savings else None,
                    estimated_savings_high_seconds=savings.high_seconds if savings else None,
                    estimated_savings_basis=savings.basis if savings else None,
                    estimated_savings_unknown=savings.unknown if savings else True,
                )
            )

        analysis.status = "completed"
        analysis.runs_analyzed_count = len(adpo_runs)
        if engine.errors:
            analysis.analyzer_errors = "; ".join(engine.errors)
        analysis.completed_at = datetime.now(timezone.utc)
        logger.info(
            "analysis completed",
            extra=log_extra(
                repository_id=repository_id,
                analysis_id=analysis.id,
                runs_analyzed=len(adpo_runs),
                findings=len(findings),
            ),
        )
    except Exception as exc:  # noqa: BLE001 - persist failure state rather than losing the record
        analysis.status = "failed"
        analysis.error_message = str(exc)
        analysis.completed_at = datetime.now(timezone.utc)
        logger.exception(
            "analysis failed", extra=log_extra(repository_id=repository_id, analysis_id=analysis.id)
        )

    db.commit()
    db.refresh(analysis)
    return analysis
