"""Endpoints that read live from the GitHub API (not our DB) - used to let
the user pick which of their already-installed repos to connect."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.config import Settings, get_settings
from app.models.repository import Repository
from app.models.user import User
from app.schemas.github import GitHubRepositoryOut
from app.services.github.app_auth import get_installation_token
from app.services.github.client import GitHubClient
from app.services.github.exceptions import GitHubError

router = APIRouter(prefix="/github", tags=["github"])


@router.get("/repositories", response_model=list[GitHubRepositoryOut])
def list_github_repositories(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> list[GitHubRepositoryOut]:
    if not user.installations:
        return []

    connected_ids = set(
        db.execute(select(Repository.github_repo_id)).scalars().all()
    )

    results: list[GitHubRepositoryOut] = []
    for installation in user.installations:
        try:
            token = get_installation_token(settings, installation.installation_id)
            with GitHubClient(token, base_url=settings.github_api_base_url) as client:
                raw_repos = client.list_installation_repositories()
        except GitHubError as exc:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY, f"failed to list repos for installation {installation.installation_id}: {exc}"
            ) from exc

        for raw in raw_repos:
            results.append(
                GitHubRepositoryOut(
                    github_repo_id=raw["id"],
                    owner_login=raw["owner"]["login"],
                    name=raw["name"],
                    full_name=raw["full_name"],
                    private=raw["private"],
                    default_branch=raw.get("default_branch", "main"),
                    html_url=raw["html_url"],
                    installation_id=installation.installation_id,
                    already_connected=raw["id"] in connected_ids,
                )
            )
    return results
