"""GitHub App connection models.

The brief's suggested schema names a single `GitHubAccount` entity, but the
GitHub App auth model (see backend/README.md "Authentication architecture")
actually has two distinct, independently-expiring credentials that must not
be conflated:

  * `GitHubToken`        - the user-to-server OAuth token. Identifies *who*
                            is signed in and lists repos they personally
                            granted. One per `User`.
  * `GitHubInstallation` - a GitHub App installation on a user or org
                            account. Grants access to a fixed set of repos
                            via short-lived installation tokens minted
                            on-demand (never stored). One user may have
                            multiple installations (personal + orgs).

Splitting these avoids conflating "who is logged in" with "which repos are
reachable," which is exactly the distinction GitHub Apps are designed
around.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class GitHubToken(TimestampMixin, Base):
    """User-to-server OAuth token for the signed-in user.

    `access_token`/`refresh_token` are stored encrypted at rest (see
    `app.core.security.encrypt_token`/`decrypt_token`) and are never
    serialized in any API response schema.
    """

    __tablename__ = "github_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )

    access_token_encrypted: Mapped[str] = mapped_column(String(2048), nullable=False)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    token_type: Mapped[str] = mapped_column(String(32), default="bearer", nullable=False)
    scope: Mapped[str | None] = mapped_column(String(512), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    refresh_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship(back_populates="github_token")


class GitHubInstallation(TimestampMixin, Base):
    """A GitHub App installation on a user or organization account."""

    __tablename__ = "github_installations"

    id: Mapped[int] = mapped_column(primary_key=True)
    installation_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    account_login: Mapped[str] = mapped_column(String(255), nullable=False)
    account_type: Mapped[str] = mapped_column(String(32), nullable=False)  # "User" | "Organization"

    connected_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    connected_by_user: Mapped["User"] = relationship(back_populates="installations")
    repositories: Mapped[list["Repository"]] = relationship(
        back_populates="installation", cascade="all, delete-orphan"
    )
