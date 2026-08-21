"""Shared test fixtures.

Tests run against an isolated SQLite file DB, not the local Postgres
container, so the suite is fast, hermetic, and never touches dev/prod data.
`Base.metadata.create_all()` (not Alembic) builds the schema for tests -
the Alembic migration itself is verified separately by actually applying it
to Postgres (see backend/README.md "Running the backend locally").
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("TOKEN_ENCRYPTION_KEY", "kY2erqEBpZ8vP9dvdxrKWCbAV3jRz0v3g9k3O1_gL1I=")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "analyzer"))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.core.security import create_session_cookie
from app.db.base import Base
from app.main import app
from app.models.github_account import GitHubInstallation
from app.models.user import User


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture()
def client(db_session):
    def _get_db_override():
        yield db_session

    app.dependency_overrides[get_db] = _get_db_override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def user(db_session) -> User:
    u = User(github_user_id=12345, github_login="octocat", avatar_url=None, email="octocat@example.com")
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    return u


@pytest.fixture()
def installation(db_session, user) -> GitHubInstallation:
    inst = GitHubInstallation(
        installation_id=987654, account_login="octo-org", account_type="Organization", connected_by_user_id=user.id
    )
    db_session.add(inst)
    db_session.commit()
    db_session.refresh(inst)
    return inst


@pytest.fixture()
def authed_client(client, user):
    client.cookies.set("adpo_session", create_session_cookie(user.id))
    return client
