from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class WorkflowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    github_workflow_id: int
    name: str
    path: str
    state: str
