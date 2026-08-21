from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.job import Job
from app.models.repository import Repository
from app.models.step import Step
from app.models.workflow import Workflow
from app.models.workflow_run import WorkflowRun


def _make_repository(db_session, installation, github_repo_id=1):
    repo = Repository(
        github_repo_id=github_repo_id,
        installation_id=installation.id,
        owner_login="octo-org",
        name="widgets",
        full_name="octo-org/widgets",
        default_branch="main",
        private=False,
        html_url="https://github.com/octo-org/widgets",
    )
    db_session.add(repo)
    db_session.commit()
    db_session.refresh(repo)
    return repo


def test_repository_github_repo_id_is_unique(db_session, installation):
    _make_repository(db_session, installation, github_repo_id=42)
    dup = Repository(
        github_repo_id=42,
        installation_id=installation.id,
        owner_login="octo-org",
        name="other",
        full_name="octo-org/other",
        default_branch="main",
        private=False,
        html_url="https://github.com/octo-org/other",
    )
    db_session.add(dup)
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_workflow_run_unique_on_run_id_and_attempt(db_session, installation):
    repo = _make_repository(db_session, installation)
    workflow = Workflow(repository_id=repo.id, github_workflow_id=1, name="CI", path=".github/workflows/ci.yml")
    db_session.add(workflow)
    db_session.commit()

    run1 = WorkflowRun(
        repository_id=repo.id,
        workflow_id=workflow.id,
        github_run_id=100,
        run_attempt=1,
        run_number=1,
        branch="main",
        event="push",
        status="completed",
        conclusion="success",
        run_created_at=datetime.now(timezone.utc),
    )
    db_session.add(run1)
    db_session.commit()

    # A rerun (same github_run_id, attempt 2) must be storable alongside
    # attempt 1, not silently rejected or merged.
    run2 = WorkflowRun(
        repository_id=repo.id,
        workflow_id=workflow.id,
        github_run_id=100,
        run_attempt=2,
        run_number=1,
        branch="main",
        event="push",
        status="completed",
        conclusion="success",
        run_created_at=datetime.now(timezone.utc),
    )
    db_session.add(run2)
    db_session.commit()  # must not raise

    dup_attempt = WorkflowRun(
        repository_id=repo.id,
        workflow_id=workflow.id,
        github_run_id=100,
        run_attempt=2,
        run_number=1,
        branch="main",
        event="push",
        status="completed",
        conclusion="success",
        run_created_at=datetime.now(timezone.utc),
    )
    db_session.add(dup_attempt)
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_workflow_run_survives_workflow_deletion(db_session, installation):
    """Deleting a Workflow row must not cascade-delete its runs - it should
    null out `workflow_id` instead, per `ondelete='SET NULL'`."""
    repo = _make_repository(db_session, installation)
    workflow = Workflow(repository_id=repo.id, github_workflow_id=1, name="CI", path=".github/workflows/ci.yml")
    db_session.add(workflow)
    db_session.commit()

    run = WorkflowRun(
        repository_id=repo.id,
        workflow_id=workflow.id,
        github_run_id=200,
        run_attempt=1,
        run_number=1,
        branch="main",
        event="push",
        status="completed",
        conclusion="success",
        run_created_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    db_session.commit()

    db_session.delete(workflow)
    db_session.commit()

    db_session.refresh(run)
    assert run.workflow_id is None


def test_job_and_step_cascade_delete_with_run(db_session, installation):
    repo = _make_repository(db_session, installation)
    run = WorkflowRun(
        repository_id=repo.id,
        github_run_id=300,
        run_attempt=1,
        run_number=1,
        branch="main",
        event="push",
        status="completed",
        conclusion="success",
        run_created_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    db_session.commit()

    job = Job(workflow_run_id=run.id, github_job_id=1, name="build", status="completed", conclusion="success")
    db_session.add(job)
    db_session.commit()

    step = Step(job_id=job.id, number=1, name="Checkout", status="completed", conclusion="success")
    db_session.add(step)
    db_session.commit()

    db_session.delete(run)
    db_session.commit()

    assert db_session.get(Job, job.id) is None
    assert db_session.get(Step, step.id) is None


def test_step_unique_on_job_and_number(db_session, installation):
    repo = _make_repository(db_session, installation)
    run = WorkflowRun(
        repository_id=repo.id,
        github_run_id=400,
        run_attempt=1,
        run_number=1,
        branch="main",
        event="push",
        status="completed",
        conclusion="success",
        run_created_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    db_session.commit()
    job = Job(workflow_run_id=run.id, github_job_id=2, name="build", status="completed", conclusion="success")
    db_session.add(job)
    db_session.commit()

    db_session.add(Step(job_id=job.id, number=1, name="Checkout", status="completed", conclusion="success"))
    db_session.commit()
    db_session.add(Step(job_id=job.id, number=1, name="Checkout dup", status="completed", conclusion="success"))
    with pytest.raises(IntegrityError):
        db_session.commit()
