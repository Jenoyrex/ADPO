from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class Job(TimestampMixin, Base):
    """A job within a workflow run.

    `needs` mirrors `adpo.models.Job.needs` (the analyzer's dependency-graph
    input for `ParallelizationAnalyzer`) but GitHub's "list jobs for a
    workflow run" REST API does not return it - that only exists in the
    workflow YAML. This MVP does not fetch/parse workflow YAML (see
    backend/README.md "Known limitations": doing so would mean handling
    repository source code, which the architecture explicitly avoids), so
    `needs` is always stored as `[]` for real synced data. The column
    exists so a future, explicit enrichment step can populate it without a
    schema change.
    """

    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("github_job_id", name="uq_jobs_github_job_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    github_job_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    workflow_run_id: Mapped[int] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    conclusion: Mapped[str | None] = mapped_column(String(32), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    runs_on: Mapped[str | None] = mapped_column(String(255), nullable=True)
    run_attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    needs: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    workflow_run: Mapped["WorkflowRun"] = relationship(back_populates="jobs")
    steps: Mapped[list["Step"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="Step.number"
    )
