from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class WorkflowRun(TimestampMixin, Base):
    """One attempt of one workflow run.

    GitHub reruns share the same `github_run_id` across attempts, so the
    natural key is `(github_run_id, run_attempt)`, not `github_run_id`
    alone - otherwise a rerun would silently overwrite the original
    attempt's data instead of being stored alongside it.
    """

    __tablename__ = "workflow_runs"
    __table_args__ = (
        UniqueConstraint("github_run_id", "run_attempt", name="uq_workflow_runs_github_run_attempt"),
        Index("ix_workflow_runs_workflow_created", "workflow_id", "created_at"),
        Index("ix_workflow_runs_repository_created", "repository_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    github_run_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    run_attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Nullable: a run can reference a workflow definition that no longer
    # exists (deleted workflow file); we keep the run's history either way.
    workflow_id: Mapped[int | None] = mapped_column(
        ForeignKey("workflows.id", ondelete="SET NULL"), nullable=True, index=True
    )

    run_number: Mapped[int] = mapped_column(Integer, nullable=False)
    branch: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    event: Mapped[str] = mapped_column(String(64), nullable=False, default="unknown")
    status: Mapped[str] = mapped_column(String(32), nullable=False)  # queued|in_progress|completed
    conclusion: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # GitHub-side timestamps, always stored as UTC-aware datetimes.
    run_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    run_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    repository: Mapped["Repository"] = relationship(back_populates="runs")
    workflow: Mapped["Workflow | None"] = relationship(back_populates="runs")
    jobs: Mapped[list["Job"]] = relationship(
        back_populates="workflow_run", cascade="all, delete-orphan", order_by="Job.github_job_id"
    )
