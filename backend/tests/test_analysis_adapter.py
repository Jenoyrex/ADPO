"""Verifies the DB-row -> `adpo.models` adapter and that running the real,
locked `adpo.AnalysisEngine` against synced data produces and persists
findings correctly - the actual Phase 2F integration point."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from adpo.models import WorkflowRun as AdpoWorkflowRun

from app.models.job import Job
from app.models.repository import Repository
from app.models.step import Step
from app.models.workflow import Workflow
from app.models.workflow_run import WorkflowRun
from app.services.analysis.adapter import workflow_run_to_adpo
from app.services.analysis.analysis_service import run_analysis


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


def _slow_run(db_session, repo, workflow, github_run_id, duration_seconds=700):
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    run = WorkflowRun(
        repository_id=repo.id,
        workflow_id=workflow.id,
        github_run_id=github_run_id,
        run_attempt=1,
        run_number=github_run_id,
        branch="main",
        event="push",
        status="completed",
        conclusion="success",
        run_created_at=start,
    )
    db_session.add(run)
    db_session.flush()

    job = Job(
        workflow_run_id=run.id,
        github_job_id=github_run_id * 10,
        name="build",
        status="completed",
        conclusion="success",
        started_at=start,
        completed_at=start + timedelta(seconds=duration_seconds),
        runs_on="ubuntu-latest",
    )
    db_session.add(job)
    db_session.flush()

    step = Step(
        job_id=job.id,
        number=1,
        name="Run tests",
        status="completed",
        conclusion="success",
        started_at=start,
        completed_at=start + timedelta(seconds=duration_seconds),
    )
    db_session.add(step)
    db_session.commit()
    return run


def test_adapter_maps_workflow_run_fields(db_session, installation):
    repo = _make_repository(db_session, installation)
    workflow = Workflow(repository_id=repo.id, github_workflow_id=1, name="CI", path=".github/workflows/ci.yml")
    db_session.add(workflow)
    db_session.commit()
    run = _slow_run(db_session, repo, workflow, github_run_id=100)
    db_session.refresh(run)

    adpo_run = workflow_run_to_adpo(run)

    assert isinstance(adpo_run, AdpoWorkflowRun)
    assert adpo_run.run_id == "100"
    assert adpo_run.workflow_name == "CI"
    assert adpo_run.branch == "main"
    assert len(adpo_run.jobs) == 1
    assert adpo_run.jobs[0].name == "build"
    assert adpo_run.jobs[0].duration_seconds == 700
    assert len(adpo_run.jobs[0].steps) == 1


def test_adapter_leaves_step_uses_and_run_fields_empty(db_session, installation):
    """Real GitHub Jobs API data never populates `uses`/`run`/`with_params`
    (see app/models/step.py) - the adapter must not invent them."""
    repo = _make_repository(db_session, installation)
    workflow = Workflow(repository_id=repo.id, github_workflow_id=1, name="CI", path=".github/workflows/ci.yml")
    db_session.add(workflow)
    db_session.commit()
    run = _slow_run(db_session, repo, workflow, github_run_id=101)
    db_session.refresh(run)

    adpo_run = workflow_run_to_adpo(run)
    step = adpo_run.jobs[0].steps[0]
    assert step.uses is None
    assert step.run is None
    assert step.with_params == {}


def test_run_analysis_produces_and_persists_findings(db_session, installation):
    repo = _make_repository(db_session, installation)
    workflow = Workflow(repository_id=repo.id, github_workflow_id=1, name="CI", path=".github/workflows/ci.yml")
    db_session.add(workflow)
    db_session.commit()
    # 700s clears SlowJobAnalyzer's absolute_severe_seconds=600s threshold
    # even with a single sample (min_sample_size=1).
    _slow_run(db_session, repo, workflow, github_run_id=200, duration_seconds=700)

    analysis = run_analysis(db_session, repo.id)

    assert analysis.status == "completed"
    assert analysis.runs_analyzed_count == 1
    assert len(analysis.findings) >= 1
    slow_job_findings = [f for f in analysis.findings if f.analyzer_type == "slow_job"]
    assert len(slow_job_findings) == 1
    finding = slow_job_findings[0]
    assert finding.severity in {"low", "medium", "high", "critical"}
    assert finding.confidence in {"low", "medium", "high"}
    assert "build" in finding.title or "build" in finding.description


def test_run_analysis_with_no_runs_is_a_no_op(db_session, installation):
    repo = _make_repository(db_session, installation)

    analysis = run_analysis(db_session, repo.id)

    assert analysis.status == "completed"
    assert analysis.runs_analyzed_count == 0
    assert analysis.findings == []


def test_run_analysis_scopes_across_multiple_workflows(db_session, installation):
    """The engine must be handed the repo's full multi-workflow history in
    one call and rely on its own internal per-workflow grouping, per the
    architecture note in app/models/analysis.py."""
    repo = _make_repository(db_session, installation)
    ci = Workflow(repository_id=repo.id, github_workflow_id=1, name="CI", path=".github/workflows/ci.yml")
    release = Workflow(repository_id=repo.id, github_workflow_id=2, name="Release", path=".github/workflows/release.yml")
    db_session.add_all([ci, release])
    db_session.commit()

    _slow_run(db_session, repo, ci, github_run_id=300, duration_seconds=700)
    _slow_run(db_session, repo, release, github_run_id=301, duration_seconds=650)

    analysis = run_analysis(db_session, repo.id)

    assert analysis.runs_analyzed_count == 2
    slow_job_findings = [f for f in analysis.findings if f.analyzer_type == "slow_job"]
    assert len(slow_job_findings) == 2  # one per workflow, not blended together
