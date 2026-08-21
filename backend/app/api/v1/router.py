from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import auth, github, repositories

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(github.router)
api_router.include_router(repositories.router)
