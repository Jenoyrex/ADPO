"""Ingestion service tests: idempotency, multi-workflow/run handling, and
resilience to missing jobs/steps - using a fake GitHub client so these
tests exercise only the normalize-and-upsert logic, not HTTP."""
from __future__ import annotations

from sqlalchemy import select

from app.models.job import Job
from app.models.repository import Repository
from app.models.step import Step
from app.models.workflow import Workflow
from app.models.workflow_run import WorkflowRun
from app.services.ingestion.sync_service import sync_repository


class FakeGitHubClient:
    def __init__(self, workflows, runs, jobs_by_run_id, raise_for_run_ids=frozenset()):
        self._workflows = workflows
        self._runs = runs
        self._jobs_by_run_id = jobs_by_run_id
        self._raise_for_run_ids = raise_for_run_ids

    def list_workflows(self, owner, repo):
        return self._workflows

    def list_workflow_runs(self, owner, repo, workflow_id=None, per_page=100):
        return iter(self._runs)

    def list_jobs_for_run(self, owner, repo, run_id, run_attempt=None):
        if run_id in self._raise_for_run_ids:
            raise RuntimeError(f"simulated GitHub error for run {run_id}")
        return self._jobs_by_run_id.get(run_id, [])


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


def _job(job_id, name="build", steps=None):
    return {
        "id": job_id,
        "name": name,
        "status": "completed",
        "conclusion": "success",
        "started_at": "2024-01-01T00:00:00Z",
        "completed_at": "2024-01-01T00:05:00Z",
        "runner_name": "ubuntu-latest",
        "run_attempt": 1,
        "steps": steps if steps is not None else [],
    }


def _step(number, name="Checkout"):
    return {
        "number": number,
        "name": name,
        "status": "completed",
        "conclusion": "success",
        "started_at": "2024-01-01T00:00:00Z",
        "completed_at": "2024-01-01T00:00:30Z",
    }


def _run(run_id, workflow_id, run_number=1, run_attempt=1, branch="main"):
    return {
        "id": run_id,
        "workflow_id": workflow_id,
        "run_number": run_number,
        "run_attempt": run_attempt,
        "head_branch": branch,
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-01T00:06:00Z",
    }


def test_sync_creates_workflows_runs_jobs_steps(db_session, installation):
    repo = _make_repository(db_session, installation)
    client = FakeGitHubClient(
        workflows=[{"id": 1, "name": "CI", "path": ".github/workflows/ci.yml", "state": "active"}],
        runs=[_run(100, workflow_id=1)],
        jobs_by_run_id={100: [_job(1000, steps=[_step(1), _step(2)])]},
    )

    result = sync_repository(db_session, repo, client)

    assert result.status == "completed"
    assert result.workflows_synced == 1
    assert result.runs_synced == 1
    assert result.jobs_synced == 1
    assert result.steps_synced == 2

    assert db_session.execute(select(Workflow)).scalars().one().name == "CI"
    assert db_session.execute(select(WorkflowRun)).scalars().one().github_run_id == 100
    assert db_session.execute(select(Job)).scalars().one().name == "build"
    assert len(db_session.execute(select(Step)).scalars().all()) == 2


def test_sync_is_idempotent(db_session, installation):
    repo = _make_repository(db_session, installation)
    client = FakeGitHubClient(
        workflows=[{"id": 1, "name": "CI", "path": ".github/workflows/ci.yml", "state": "active"}],
        runs=[_run(100, workflow_id=1)],
        jobs_by_run_id={100: [_job(1000, steps=[_step(1)])]},
    )

    sync_repository(db_session, repo, client)
    sync_repository(db_session, repo, client)  # re-run with identical data

    assert len(db_session.execute(select(Workflow)).scalars().all()) == 1
    assert len(db_session.execute(select(WorkflowRun)).scalars().all()) == 1
    assert len(db_session.execute(select(Job)).scalars().all()) == 1
    assert len(db_session.execute(select(Step)).scalars().all()) == 1


def test_sync_updates_renamed_workflow_in_place(db_session, installation):
    repo = _make_repository(db_session, installation)
    client_v1 = FakeGitHubClient(
        workflows=[{"id": 1, "name": "CI", "path": ".github/workflows/ci.yml", "state": "active"}],
        runs=[],
        jobs_by_run_id={},
    )
    sync_repository(db_session, repo, client_v1)

    client_v2 = FakeGitHubClient(
        workflows=[{"id": 1, "name": "Continuous Integration", "path": ".github/workflows/ci.yml", "state": "active"}],
        runs=[],
        jobs_by_run_id={},
    )
    sync_repository(db_session, repo, client_v2)

    workflows = db_session.execute(select(Workflow)).scalars().all()
    assert len(workflows) == 1
    assert workflows[0].name == "Continuous Integration"


def test_sync_multiple_workflows_kept_separate(db_session, installation):
    repo = _make_repository(db_session, installation)
    client = FakeGitHubClient(
        workflows=[
            {"id": 1, "name": "CI", "path": ".github/workflows/ci.yml", "state": "active"},
            {"id": 2, "name": "Release", "path": ".github/workflows/release.yml", "state": "active"},
        ],
        runs=[_run(100, workflow_id=1), _run(200, workflow_id=2)],
        jobs_by_run_id={100: [_job(1000)], 200: [_job(2000)]},
    )

    result = sync_repository(db_session, repo, client)

    assert result.workflows_synced == 2
    assert result.runs_synced == 2
    runs = db_session.execute(select(WorkflowRun)).scalars().all()
    workflow_ids = {r.workflow.github_workflow_id for r in runs}
    assert workflow_ids == {1, 2}


def test_sync_stores_rerun_attempts_separately(db_session, installation):
    """A rerun gets its own job IDs from GitHub - even for attempts of the
    same run, `github_job_id` is globally unique - so `list_jobs_for_run`
    must be consulted per attempt rather than assumed identical."""
    repo = _make_repository(db_session, installation)

    class RerunAwareGitHubClient(FakeGitHubClient):
        def list_jobs_for_run(self, owner, repo, run_id, run_attempt=None):
            job_id = 1000 if run_attempt == 1 else 1001
            return [_job(job_id)]

    client = RerunAwareGitHubClient(
        workflows=[{"id": 1, "name": "CI", "path": ".github/workflows/ci.yml", "state": "active"}],
        runs=[_run(100, workflow_id=1, run_attempt=1), _run(100, workflow_id=1, run_attempt=2)],
        jobs_by_run_id={},
    )

    result = sync_repository(db_session, repo, client)

    assert result.status == "completed"
    runs = db_session.execute(select(WorkflowRun).where(WorkflowRun.github_run_id == 100)).scalars().all()
    assert {r.run_attempt for r in runs} == {1, 2}
    assert {r.jobs[0].github_job_id for r in runs} == {1000, 1001}


def test_sync_handles_missing_jobs_without_aborting_whole_sync(db_session, installation):
    repo = _make_repository(db_session, installation)
    client = FakeGitHubClient(
        workflows=[{"id": 1, "name": "CI", "path": ".github/workflows/ci.yml", "state": "active"}],
        runs=[_run(100, workflow_id=1), _run(101, workflow_id=1, run_number=2)],
        jobs_by_run_id={101: [_job(2000)]},  # run 100 has no entry -> list_jobs_for_run raises
        raise_for_run_ids=frozenset({100}),
    )

    result = sync_repository(db_session, repo, client)

    assert result.status == "completed"  # a per-run job failure must not fail the whole sync
    assert result.runs_synced == 2
    assert len(result.errors) == 1
    run_100 = db_session.execute(select(WorkflowRun).where(WorkflowRun.github_run_id == 100)).scalar_one()
    assert run_100.jobs == []  # run persisted even though its jobs could not be fetched
    run_101 = db_session.execute(select(WorkflowRun).where(WorkflowRun.github_run_id == 101)).scalar_one()
    assert len(run_101.jobs) == 1


def test_sync_handles_job_with_no_steps(db_session, installation):
    repo = _make_repository(db_session, installation)
    client = FakeGitHubClient(
        workflows=[{"id": 1, "name": "CI", "path": ".github/workflows/ci.yml", "state": "active"}],
        runs=[_run(100, workflow_id=1)],
        jobs_by_run_id={100: [_job(1000, steps=[])]},
    )

    result = sync_repository(db_session, repo, client)

    assert result.status == "completed"
    assert result.jobs_synced == 1
    assert result.steps_synced == 0
    job = db_session.execute(select(Job)).scalars().one()
    assert job.steps == []


def test_sync_handles_run_with_unknown_workflow_id(db_session, installation):
    """A run referencing a workflow_id we haven't seen (e.g. deleted before
    sync) must still be stored, with workflow_id left NULL."""
    repo = _make_repository(db_session, installation)
    client = FakeGitHubClient(
        workflows=[],
        runs=[_run(100, workflow_id=999)],
        jobs_by_run_id={100: []},
    )

    result = sync_repository(db_session, repo, client)

    assert result.status == "completed"
    run = db_session.execute(select(WorkflowRun)).scalars().one()
    assert run.workflow_id is None
