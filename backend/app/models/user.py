from __future__ import annotations

from sqlalchemy import BigInteger, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class User(TimestampMixin, Base):
    """A person who has signed in to ADPO with GitHub.

    Distinct from `GitHubInstallation`: a `User` is our own account,
    identified by their stable GitHub numeric user ID. One user may connect
    multiple GitHub App installations (e.g. a personal account and an org).
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    github_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    github_login: Mapped[str] = mapped_column(String(255), nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)

    github_token: Mapped["GitHubToken | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    installations: Mapped[list["GitHubInstallation"]] = relationship(
        back_populates="connected_by_user", cascade="all, delete-orphan"
    )
