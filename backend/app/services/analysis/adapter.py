"""Adapter: normalized DB rows -> `adpo.models` objects.

This is the ONLY place the backend is allowed to construct `adpo.models`
objects. It exists specifically so `adpo` never needs to know our ORM,
GitHub's wire format, or anything else about the backend - per the fixed
architecture:

    GitHub API -> GitHub service -> normalized models -> DB -> [adapter] -> analyzer

We go DB row -> `adpo.models` directly rather than DB row -> raw GitHub
JSON -> `adpo.ingest.parse_workflow_run`, because `adpo.ingest` exists to
parse *untyped, string-timestamped* raw GitHub JSON; our DB rows already
carry typed, timezone-aware `datetime`s and validated fields, so re-
serializing them to strings just to re-parse them would add a lossy round
trip for no benefit. `adpo.ingest` remains what real raw-GitHub-JSON
callers (e.g. a future "analyze without syncing" path) would use.
"""
from __future__ import annotations

from adpo.models import Job as AdpoJob
from adpo.models import Step as AdpoStep
from adpo.models import WorkflowRun as AdpoWorkflowRun

from app.models.job import Job as DbJob
from app.models.step import Step as DbStep
from app.models.workflow_run import WorkflowRun as DbWorkflowRun


def step_to_adpo(step: DbStep) -> AdpoStep:
    return AdpoStep(
        name=step.name,
        number=step.number,
        status=step.status,
        conclusion=step.conclusion,
        started_at=step.started_at,
        completed_at=step.completed_at,
        # `uses`/`with_params`/`run` are not populated from real synced
        # data - see app/models/step.py docstring for why.
        uses=None,
        with_params={},
        run=None,
    )


def job_to_adpo(job: DbJob) -> AdpoJob:
    return AdpoJob(
        job_id=str(job.github_job_id),
        name=job.name,
        status=job.status,
        conclusion=job.conclusion,
        started_at=job.started_at,
        completed_at=job.completed_at,
        steps=[step_to_adpo(s) for s in job.steps],
        needs=list(job.needs or []),
        runs_on=job.runs_on,
        run_attempt=job.run_attempt,
    )


def workflow_run_to_adpo(run: DbWorkflowRun) -> AdpoWorkflowRun:
    workflow_name = run.workflow.name if run.workflow is not None else "unknown-workflow"
    return AdpoWorkflowRun(
        run_id=str(run.github_run_id),
        workflow_name=workflow_name,
        run_number=run.run_number,
        branch=run.branch,
        event=run.event,
        created_at=run.run_created_at,
        conclusion=run.conclusion,
        jobs=[job_to_adpo(j) for j in run.jobs],
        run_attempt=run.run_attempt,
        updated_at=run.run_updated_at,
    )


def workflow_runs_to_adpo(runs: list[DbWorkflowRun]) -> list[AdpoWorkflowRun]:
    return [workflow_run_to_adpo(r) for r in runs]
