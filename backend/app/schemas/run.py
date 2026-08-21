from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class StepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    number: int
    name: str
    status: str
    conclusion: str | None
    started_at: datetime | None
    completed_at: datetime | None


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    github_job_id: int
    name: str
    status: str
    conclusion: str | None
    started_at: datetime | None
    completed_at: datetime | None
    runs_on: str | None
    steps: list[StepOut] = []


class WorkflowRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    github_run_id: int
    run_number: int
    run_attempt: int
    branch: str
    event: str
    status: str
    conclusion: str | None
    run_created_at: datetime
    run_updated_at: datetime | None
    workflow_id: int | None
