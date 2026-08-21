from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class RepositoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    github_repo_id: int
    owner_login: str
    name: str
    full_name: str
    default_branch: str
    private: bool
    html_url: str
    last_synced_at: datetime | None


class RepositoryConnectRequest(BaseModel):
    github_repo_id: int


class SyncStatusOut(BaseModel):
    repository_id: int
    status: str
    workflows_synced: int
    runs_synced: int
    jobs_synced: int
    steps_synced: int
    started_at: datetime
    completed_at: datetime
