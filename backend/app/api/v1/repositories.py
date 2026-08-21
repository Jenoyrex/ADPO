from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.config import Settings, get_settings
from app.models.analysis import Analysis
from app.models.github_account import GitHubInstallation
from app.models.repository import Repository
from app.models.user import User
from app.models.workflow import Workflow
from app.models.workflow_run import WorkflowRun
from app.schemas.finding import AnalysisOut, FindingOut
from app.schemas.repository import RepositoryConnectRequest, RepositoryOut, SyncStatusOut
from app.schemas.run import WorkflowRunOut
from app.schemas.workflow import WorkflowOut
from app.services.analysis.analysis_service import run_analysis
from app.services.github.app_auth import get_installation_token
from app.services.github.client import GitHubClient
from app.services.github.exceptions import GitHubError
from app.services.ingestion.sync_service import sync_repository

router = APIRouter(prefix="/repositories", tags=["repositories"])


def _owned_installation_ids(db: Session, user: User) -> list[int]:
    return [i.id for i in user.installations]


def _get_owned_repository(db: Session, repository_id: int, user: User) -> Repository:
    """Authorization boundary: a repository is only visible/actionable if
    it belongs to an installation the current user connected."""
    repo = db.execute(
        select(Repository)
        .join(GitHubInstallation, Repository.installation_id == GitHubInstallation.id)
        .where(Repository.id == repository_id, GitHubInstallation.connected_by_user_id == user.id)
    ).scalar_one_or_none()
    if repo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "repository not found")
    return repo


@router.get("", response_model=list[RepositoryOut])
def list_repositories(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[Repository]:
    installation_ids = _owned_installation_ids(db, user)
    if not installation_ids:
        return []
    return list(
        db.execute(
            select(Repository).where(Repository.installation_id.in_(installation_ids))
        ).scalars()
    )


@router.post("", response_model=RepositoryOut, status_code=status.HTTP_201_CREATED)
def connect_repository(
    payload: RepositoryConnectRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Repository:
    existing = db.execute(
        select(Repository).where(Repository.github_repo_id == payload.github_repo_id)
    ).scalar_one_or_none()
    if existing is not None:
        _get_owned_repository(db, existing.id, user)  # 404s if some other user owns it
        return existing

    for installation in user.installations:
        try:
            token = get_installation_token(settings, installation.installation_id)
            with GitHubClient(token, base_url=settings.github_api_base_url) as client:
                raw_repos = client.list_installation_repositories()
        except GitHubError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"could not verify repo access: {exc}") from exc

        match = next((r for r in raw_repos if r["id"] == payload.github_repo_id), None)
        if match is not None:
            repo = Repository(
                github_repo_id=match["id"],
                installation_id=installation.id,
                owner_login=match["owner"]["login"],
                name=match["name"],
                full_name=match["full_name"],
                default_branch=match.get("default_branch", "main"),
                private=match["private"],
                html_url=match["html_url"],
            )
            db.add(repo)
            db.commit()
            db.refresh(repo)
            return repo

    raise HTTPException(
        status.HTTP_403_FORBIDDEN, "repository is not accessible through any of your GitHub App installations"
    )


@router.post("/{repository_id}/sync", response_model=SyncStatusOut)
def sync_repository_endpoint(
    repository_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SyncStatusOut:
    repository = _get_owned_repository(db, repository_id, user)
    installation = db.get(GitHubInstallation, repository.installation_id)

    try:
        token = get_installation_token(settings, installation.installation_id)
    except GitHubError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"could not authenticate with GitHub: {exc}") from exc

    with GitHubClient(token, base_url=settings.github_api_base_url) as client:
        result = sync_repository(db, repository, client)

    if result.status == "failed":
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"sync failed: {'; '.join(result.errors)}")

    return SyncStatusOut(
        repository_id=repository.id,
        status=result.status,
        workflows_synced=result.workflows_synced,
        runs_synced=result.runs_synced,
        jobs_synced=result.jobs_synced,
        steps_synced=result.steps_synced,
        started_at=result.started_at,
        completed_at=result.completed_at,
    )


@router.get("/{repository_id}/workflows", response_model=list[WorkflowOut])
def list_workflows(
    repository_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[Workflow]:
    repository = _get_owned_repository(db, repository_id, user)
    return list(db.execute(select(Workflow).where(Workflow.repository_id == repository.id)).scalars())


@router.get("/{repository_id}/runs", response_model=list[WorkflowRunOut])
def list_runs(
    repository_id: int,
    workflow_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[WorkflowRun]:
    repository = _get_owned_repository(db, repository_id, user)
    limit = max(1, min(limit, 200))

    query = select(WorkflowRun).where(WorkflowRun.repository_id == repository.id)
    if workflow_id is not None:
        query = query.where(WorkflowRun.workflow_id == workflow_id)
    query = query.order_by(WorkflowRun.run_created_at.desc()).limit(limit).offset(offset)
    return list(db.execute(query).scalars())


@router.post("/{repository_id}/analyze", response_model=AnalysisOut)
def analyze_repository(
    repository_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> AnalysisOut:
    repository = _get_owned_repository(db, repository_id, user)
    analysis = run_analysis(db, repository.id)
    if analysis.status == "failed":
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"analysis failed: {analysis.error_message}")
    return AnalysisOut(
        id=analysis.id,
        repository_id=analysis.repository_id,
        status=analysis.status,
        runs_analyzed_count=analysis.runs_analyzed_count,
        findings=[FindingOut.from_model(f) for f in analysis.findings],
    )


@router.get("/{repository_id}/findings", response_model=list[FindingOut])
def list_findings(
    repository_id: int,
    analysis_id: int | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[FindingOut]:
    repository = _get_owned_repository(db, repository_id, user)

    if analysis_id is not None:
        analysis = db.get(Analysis, analysis_id)
        if analysis is None or analysis.repository_id != repository.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "analysis not found")
    else:
        analysis = db.execute(
            select(Analysis)
            .where(Analysis.repository_id == repository.id, Analysis.status == "completed")
            .order_by(Analysis.completed_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if analysis is None:
            return []

    return [FindingOut.from_model(f) for f in analysis.findings]
