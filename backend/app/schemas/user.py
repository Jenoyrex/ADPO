from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    github_login: str
    avatar_url: str | None
    email: str | None
