from __future__ import annotations

from sqlalchemy import BigInteger, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class Workflow(TimestampMixin, Base):
    """A `.github/workflows/*.yml` definition (e.g. "CI", "Release").

    GitHub allows a workflow to be renamed or deleted without its ID
    changing, so upserts key on `(repository_id, github_workflow_id)` and
    `state` tracks deletion instead of removing the row (removing it would
    orphan its historical runs).
    """

    __tablename__ = "workflows"
    __table_args__ = (
        UniqueConstraint("repository_id", "github_workflow_id", name="uq_workflows_repository_github_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    github_workflow_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    state: Mapped[str] = mapped_column(String(32), default="active", nullable=False)  # active|deleted|disabled

    repository: Mapped["Repository"] = relationship(back_populates="workflows")
    # No delete cascade here, deliberately: runs must survive their
    # Workflow being deleted (see docstring above) via the FK's
    # `ondelete="SET NULL"` (app/models/workflow_run.py). An ORM-level
    # "delete-orphan" cascade would delete the child WorkflowRun rows
    # itself, bypassing and contradicting that DB-level behavior.
    runs: Mapped[list["WorkflowRun"]] = relationship(back_populates="workflow")
