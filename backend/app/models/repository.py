from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class Repository(TimestampMixin, Base):
    __tablename__ = "repositories"
    __table_args__ = (UniqueConstraint("github_repo_id", name="uq_repositories_github_repo_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    github_repo_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    installation_id: Mapped[int] = mapped_column(
        ForeignKey("github_installations.id", ondelete="CASCADE"), nullable=False, index=True
    )

    owner_login: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    default_branch: Mapped[str] = mapped_column(String(255), default="main", nullable=False)
    private: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    html_url: Mapped[str] = mapped_column(String(1024), nullable=False)

    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    installation: Mapped["GitHubInstallation"] = relationship(back_populates="repositories")
    workflows: Mapped[list["Workflow"]] = relationship(back_populates="repository", cascade="all, delete-orphan")
    runs: Mapped[list["WorkflowRun"]] = relationship(back_populates="repository", cascade="all, delete-orphan")
    analyses: Mapped[list["Analysis"]] = relationship(back_populates="repository", cascade="all, delete-orphan")
