"""Application configuration, loaded from environment variables / .env.

No secret ever has a hard-coded default that would work in production —
required secrets default to empty string and are validated lazily by the
services that need them, so `GET /health` and the test suite can still run
without a fully configured GitHub App.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- App ---
    app_name: str = "ADPO Backend"
    environment: str = "development"
    secret_key: str = "dev-only-insecure-secret-key"
    cors_origins: str = "http://localhost:3000"
    # Comma-separated list of Host headers TrustedHostMiddleware will accept.
    # Empty (the default) means the middleware is not added at all, so local
    # dev/tests are unaffected until this is explicitly set for production.
    allowed_hosts: str = ""

    # --- Database ---
    database_url: str = "postgresql+psycopg://adpo:adpo@localhost:5433/adpo"

    @field_validator("database_url")
    @classmethod
    def _force_psycopg_driver(cls, v: str) -> str:
        # Managed hosts (e.g. Render) hand out bare postgresql:// / postgres://
        # URLs. SQLAlchemy resolves an unqualified postgresql:// scheme to the
        # psycopg2 dialect, but this project only ships psycopg v3 - so force
        # the +psycopg driver when the URL doesn't already name one.
        if v.startswith("postgresql://"):
            return "postgresql+psycopg://" + v[len("postgresql://"):]
        if v.startswith("postgres://"):
            return "postgresql+psycopg://" + v[len("postgres://"):]
        return v

    # --- Token encryption at rest ---
    token_encryption_key: str = ""

    # --- GitHub App ---
    github_app_id: str = ""
    github_app_client_id: str = ""
    github_app_client_secret: str = ""
    github_app_private_key: str = ""
    github_app_private_key_path: str = ""
    github_app_name: str = ""
    github_oauth_redirect_uri: str = "http://localhost:8000/api/v1/auth/github/callback"
    github_api_base_url: str = "https://api.github.com"

    frontend_success_redirect_url: str = "http://localhost:3000/dashboard"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def allowed_hosts_list(self) -> list[str]:
        return [h.strip() for h in self.allowed_hosts.split(",") if h.strip()]

    @property
    def github_app_private_key_pem(self) -> str:
        if self.github_app_private_key:
            return self.github_app_private_key.replace("\\n", "\n")
        if self.github_app_private_key_path:
            return Path(self.github_app_private_key_path).read_text(encoding="utf-8")
        return ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
