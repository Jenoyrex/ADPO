"""Schemas for data read live from the GitHub API (not our DB) -
`GET /api/v1/github/repositories`."""
from __future__ import annotations

from pydantic import BaseModel


class GitHubRepositoryOut(BaseModel):
    github_repo_id: int
    owner_login: str
    name: str
    full_name: str
    private: bool
    default_branch: str
    html_url: str
    installation_id: int
    already_connected: bool
