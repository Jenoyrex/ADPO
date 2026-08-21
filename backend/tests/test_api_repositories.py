from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.github_account import GitHubInstallation
from app.models.job import Job
from app.models.repository import Repository
from app.models.user import User
from app.models.workflow import Workflow
from app.models.workflow_run import WorkflowRun
from app.services.ingestion.sync_service import SyncResult


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


# -- authentication behavior ----------------------------------------------


def test_repositories_requires_authentication(client):
    response = client.get("/api/v1/repositories")
    assert response.status_code == 401


def test_invalid_session_cookie_is_rejected(client):
    client.cookies.set("adpo_session", "not-a-valid-signed-cookie")
    response = client.get("/api/v1/repositories")
    assert response.status_code == 401


def test_me_returns_current_user(authed_client, user):
    response = authed_client.get("/api/v1/auth/me")
    assert response.status_code == 200
    assert response.json()["github_login"] == user.github_login


# -- repository listing / connecting ---------------------------------------


def test_list_repositories_empty_for_new_user(authed_client):
    response = authed_client.get("/api/v1/repositories")
    assert response.status_code == 200
    assert response.json() == []


def test_connect_repository_success(authed_client, db_session, installation, monkeypatch):
    def fake_get_installation_token(settings, installation_id, **kwargs):
        return "fake-installation-token"

    class FakeGitHubClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def list_installation_repositories(self):
            return [
                {
                    "id": 555,
                    "owner": {"login": "octo-org"},
                    "name": "widgets",
                    "full_name": "octo-org/widgets",
                    "private": False,
                    "default_branch": "main",
                    "html_url": "https://github.com/octo-org/widgets",
                }
            ]

    monkeypatch.setattr("app.api.v1.repositories.get_installation_token", fake_get_installation_token)
    monkeypatch.setattr("app.api.v1.repositories.GitHubClient", FakeGitHubClient)

    response = authed_client.post("/api/v1/repositories", json={"github_repo_id": 555})

    assert response.status_code == 201
    body = response.json()
    assert body["github_repo_id"] == 555
    assert body["full_name"] == "octo-org/widgets"


def test_connect_repository_forbidden_when_not_installed(authed_client, installation, monkeypatch):
    def fake_get_installation_token(settings, installation_id, **kwargs):
        return "fake-installation-token"

    class FakeGitHubClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def list_installation_repositories(self):
            return []  # user's installation cannot see this repo

    monkeypatch.setattr("app.api.v1.repositories.get_installation_token", fake_get_installation_token)
    monkeypatch.setattr("app.api.v1.repositories.GitHubClient", FakeGitHubClient)

    response = authed_client.post("/api/v1/repositories", json={"github_repo_id": 999})

    assert response.status_code == 403


# -- authorization boundaries -----------------------------------------------


def test_cannot_access_another_users_repository(authed_client, db_session):
    other_user = User(github_user_id=99999, github_login="someone-else")
    db_session.add(other_user)
    db_session.commit()
    other_installation = GitHubInstallation(
        installation_id=111222, account_login="other-org", account_type="Organization", connected_by_user_id=other_user.id
    )
    db_session.add(other_installation)
    db_session.commit()
    other_repo = _make_repository(db_session, other_installation, github_repo_id=777)

    response = authed_client.get(f"/api/v1/repositories/{other_repo.id}/workflows")

    assert response.status_code == 404


def test_own_repository_workflows_accessible(authed_client, db_session, installation):
    repo = _make_repository(db_session, installation)
    workflow = Workflow(repository_id=repo.id, github_workflow_id=1, name="CI", path=".github/workflows/ci.yml")
    db_session.add(workflow)
    db_session.commit()

    response = authed_client.get(f"/api/v1/repositories/{repo.id}/workflows")

    assert response.status_code == 200
    assert response.json()[0]["name"] == "CI"


# -- sync --------------------------------------------------------------------


def test_sync_endpoint_returns_sync_status(authed_client, db_session, installation, monkeypatch):
    repo = _make_repository(db_session, installation)

    def fake_get_installation_token(settings, installation_id, **kwargs):
        return "fake-token"

    def fake_sync_repository(db, repository, client):
        return SyncResult(
            repository_id=repository.id,
            workflows_synced=2,
            runs_synced=5,
            jobs_synced=10,
            steps_synced=40,
            completed_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr("app.api.v1.repositories.get_installation_token", fake_get_installation_token)
    monkeypatch.setattr("app.api.v1.repositories.sync_repository", fake_sync_repository)

    response = authed_client.post(f"/api/v1/repositories/{repo.id}/sync")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["runs_synced"] == 5
    assert body["jobs_synced"] == 10


def test_sync_endpoint_requires_ownership(authed_client, db_session):
    response = authed_client.post("/api/v1/repositories/99999/sync")
    assert response.status_code == 404


# -- analyze / findings -------------------------------------------------------


def test_analyze_and_findings_roundtrip(authed_client, db_session, installation):
    repo = _make_repository(db_session, installation)
    workflow = Workflow(repository_id=repo.id, github_workflow_id=1, name="CI", path=".github/workflows/ci.yml")
    db_session.add(workflow)
    db_session.commit()

    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    run = WorkflowRun(
        repository_id=repo.id,
        workflow_id=workflow.id,
        github_run_id=1,
        run_attempt=1,
        run_number=1,
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
        github_job_id=1,
        name="build",
        status="completed",
        conclusion="success",
        started_at=start,
        completed_at=start + timedelta(seconds=700),
    )
    db_session.add(job)
    db_session.commit()

    analyze_response = authed_client.post(f"/api/v1/repositories/{repo.id}/analyze")
    assert analyze_response.status_code == 200
    analyze_body = analyze_response.json()
    assert analyze_body["status"] == "completed"
    assert len(analyze_body["findings"]) >= 1

    findings_response = authed_client.get(f"/api/v1/repositories/{repo.id}/findings")
    assert findings_response.status_code == 200
    findings_body = findings_response.json()
    assert len(findings_body) == len(analyze_body["findings"])
    assert findings_body[0]["analyzer_type"]
    assert "severity" in findings_body[0]


def test_findings_empty_before_any_analysis(authed_client, db_session, installation):
    repo = _make_repository(db_session, installation)
    response = authed_client.get(f"/api/v1/repositories/{repo.id}/findings")
    assert response.status_code == 200
    assert response.json() == []
