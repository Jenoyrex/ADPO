from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class Step(Base):
    """A single step within a job.

    GitHub's Jobs API step shape carries `name`/`status`/`conclusion`/
    `number`/timestamps only - not `uses`, `with:` inputs, or the `run:`
    shell command (those live in the workflow YAML/source). Per the
    "do not store repository source code" constraint, `uses`/`run` are
    intentionally left `NULL` for real synced data rather than fetched from
    source; `adpo.analyzers.DependencyInstallAnalyzer` will therefore match
    only on step `name`, not on action/command text, until a future,
    explicitly-scoped enrichment step is added. No `created_at`/
    `updated_at` here: steps are immutable once a job completes and are
    always written once by the sync service, never updated in place.
    """

    __tablename__ = "steps"
    __table_args__ = (UniqueConstraint("job_id", "number", name="uq_steps_job_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)

    number: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    conclusion: Mapped[str | None] = mapped_column(String(32), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    job: Mapped["Job"] = relationship(back_populates="steps")
