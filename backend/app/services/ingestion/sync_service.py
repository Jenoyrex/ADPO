"""Idempotent GitHub -> normalized DB sync.

This is the "GitHub integration/service -> normalized internal models ->
database" stage of the fixed architecture. It never imports `adpo` and the
analyzer never imports this module - they meet only through
`app.services.analysis.adapter`.

Idempotency strategy: every upsert keys on the GitHub-assigned ID (never on
name/path, which can be renamed) and every existing row for the repository
is pre-loaded into a dict once per sync, so re-running a sync (or retrying
after a partial failure) updates rows in place instead of duplicating them.
A partially-completed workflow run (status="in_progress", conclusion=None)
is stored as-is and simply gets updated in place on the next sync once it
finishes - there is no special-case handling needed because every field is
nullable exactly where GitHub's own data can be absent.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.logging import get_logger, log_extra
from app.models.job import Job
from app.models.repository import Repository
from app.models.step import Step
from app.models.workflow import Workflow
from app.models.workflow_run import WorkflowRun
from app.services.github.client import GitHubClient

logger = get_logger("adpo.ingestion")


@dataclass
class SyncResult:
    repository_id: int
    workflows_synced: int = 0
    runs_synced: int = 0
    jobs_synced: int = 0
    steps_synced: int = 0
    errors: list[str] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    status: str = "completed"


def _parse_github_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def sync_repository(db: Session, repository: Repository, client: GitHubClient) -> SyncResult:
    result = SyncResult(repository_id=repository.id)
    owner, repo_name = repository.owner_login, repository.name

    try:
        _sync_workflows(db, repository, client, result)
        db.flush()
        _sync_runs_and_jobs(db, repository, client, result)
        repository.last_synced_at = datetime.now(timezone.utc)
        db.commit()
    except Exception as exc:  # noqa: BLE001 - a failed sync must still leave prior progress committed
        db.rollback()
        result.status = "failed"
        result.errors.append(str(exc))
        logger.exception(
            "repository sync failed",
            extra=log_extra(repository_id=repository.id, owner=owner, repo=repo_name),
        )

    result.completed_at = datetime.now(timezone.utc)
    logger.info(
        "repository sync finished",
        extra=log_extra(
            repository_id=repository.id,
            status=result.status,
            workflows=result.workflows_synced,
            runs=result.runs_synced,
            jobs=result.jobs_synced,
            steps=result.steps_synced,
        ),
    )
    return result


def _sync_workflows(db: Session, repository: Repository, client: GitHubClient, result: SyncResult) -> None:
    existing = {
        w.github_workflow_id: w
        for w in db.execute(
            select(Workflow).where(Workflow.repository_id == repository.id)
        ).scalars()
    }

    for raw in client.list_workflows(repository.owner_login, repository.name):
        github_workflow_id = raw["id"]
        workflow = existing.get(github_workflow_id)
        if workflow is None:
            workflow = Workflow(repository_id=repository.id, github_workflow_id=github_workflow_id)
            db.add(workflow)
            existing[github_workflow_id] = workflow

        workflow.name = raw.get("name") or workflow.name or "unnamed-workflow"
        workflow.path = raw.get("path", "")
        # GitHub's workflow `state` is one of: active, deleted, disabled_*.
        workflow.state = raw.get("state", "active")
        result.workflows_synced += 1


def _sync_runs_and_jobs(db: Session, repository: Repository, client: GitHubClient, result: SyncResult) -> None:
    workflow_by_github_id = {
        w.github_workflow_id: w
        for w in db.execute(select(Workflow).where(Workflow.repository_id == repository.id)).scalars()
    }
    existing_runs = {
        (r.github_run_id, r.run_attempt): r
        for r in db.execute(
            select(WorkflowRun)
            .where(WorkflowRun.repository_id == repository.id)
            .options(selectinload(WorkflowRun.jobs))
        ).scalars()
    }

    for raw_run in client.list_workflow_runs(repository.owner_login, repository.name):
        try:
            run = _upsert_run(db, repository, workflow_by_github_id, existing_runs, raw_run)
            result.runs_synced += 1
        except Exception as exc:  # noqa: BLE001 - one malformed run must not abort the whole sync
            result.errors.append(f"run {raw_run.get('id')}: {exc}")
            logger.warning(
                "skipping run due to error",
                extra=log_extra(repository_id=repository.id, run_id=raw_run.get("id"), error=str(exc)),
            )
            continue

        try:
            jobs_synced, steps_synced = _sync_jobs_for_run(db, repository, run, raw_run, client)
            result.jobs_synced += jobs_synced
            result.steps_synced += steps_synced
        except Exception as exc:  # noqa: BLE001 - missing jobs for one run must not abort the sync
            result.errors.append(f"jobs for run {raw_run.get('id')}: {exc}")
            logger.warning(
                "skipping jobs for run due to error",
                extra=log_extra(repository_id=repository.id, run_id=raw_run.get("id"), error=str(exc)),
            )


def _upsert_run(
    db: Session,
    repository: Repository,
    workflow_by_github_id: dict[int, Workflow],
    existing_runs: dict[tuple[int, int], WorkflowRun],
    raw_run: dict[str, Any],
) -> WorkflowRun:
    github_run_id = raw_run["id"]
    run_attempt = raw_run.get("run_attempt", 1)
    key = (github_run_id, run_attempt)

    run = existing_runs.get(key)
    if run is None:
        run = WorkflowRun(repository_id=repository.id, github_run_id=github_run_id, run_attempt=run_attempt)
        db.add(run)
        existing_runs[key] = run

    workflow = workflow_by_github_id.get(raw_run.get("workflow_id"))
    run.workflow_id = workflow.id if workflow else None
    run.run_number = raw_run.get("run_number", 0)
    run.branch = raw_run.get("head_branch") or ""
    run.event = raw_run.get("event", "unknown")
    run.status = raw_run.get("status", "completed")
    run.conclusion = raw_run.get("conclusion")
    created_at = _parse_github_dt(raw_run.get("created_at") or raw_run.get("run_started_at"))
    if created_at is None:
        raise ValueError("run is missing created_at/run_started_at")
    run.run_created_at = created_at
    run.run_updated_at = _parse_github_dt(raw_run.get("updated_at"))
    db.flush()  # assign run.id if newly created, needed for job upserts
    return run


def _sync_jobs_for_run(
    db: Session, repository: Repository, run: WorkflowRun, raw_run: dict[str, Any], client: GitHubClient
) -> tuple[int, int]:
    existing_jobs = {j.github_job_id: j for j in run.jobs}

    raw_jobs = client.list_jobs_for_run(
        repository.owner_login, repository.name, raw_run["id"], run_attempt=run.run_attempt
    )

    jobs_synced = 0
    steps_synced = 0
    for raw_job in raw_jobs:
        github_job_id = raw_job["id"]
        job = existing_jobs.get(github_job_id)
        if job is None:
            job = Job(workflow_run_id=run.id, github_job_id=github_job_id)
            db.add(job)
            existing_jobs[github_job_id] = job

        job.name = raw_job.get("name", "unnamed-job")
        job.status = raw_job.get("status", "completed")
        job.conclusion = raw_job.get("conclusion")
        job.started_at = _parse_github_dt(raw_job.get("started_at"))
        job.completed_at = _parse_github_dt(raw_job.get("completed_at"))
        job.runs_on = raw_job.get("runner_name")
        job.run_attempt = raw_job.get("run_attempt", run.run_attempt)
        # `needs` is not present on the Jobs API response - see app/models/job.py.
        job.needs = job.needs or []
        db.flush()  # assign job.id if newly created, needed for step upserts
        jobs_synced += 1

        steps_synced += _sync_steps_for_job(db, job, raw_job.get("steps") or [])

    return jobs_synced, steps_synced


def _sync_steps_for_job(db: Session, job: Job, raw_steps: list[dict[str, Any]]) -> int:
    existing_steps = {s.number: s for s in job.steps}
    count = 0
    for raw_step in raw_steps:
        number = raw_step.get("number", 0)
        step = existing_steps.get(number)
        if step is None:
            step = Step(job_id=job.id, number=number)
            db.add(step)
            existing_steps[number] = step

        step.name = raw_step.get("name") or f"step-{number}"
        step.status = raw_step.get("status", "completed")
        step.conclusion = raw_step.get("conclusion")
        step.started_at = _parse_github_dt(raw_step.get("started_at"))
        step.completed_at = _parse_github_dt(raw_step.get("completed_at"))
        count += 1
    return count
