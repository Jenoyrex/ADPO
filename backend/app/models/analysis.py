from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class Analysis(TimestampMixin, Base):
    """One run of `adpo.AnalysisEngine` over a repository's stored history.

    Scoped to a repository rather than a single workflow: the engine
    natively handles multi-workflow input (`BaseAnalyzer._group_by_workflow`
    keeps each workflow's statistics separate internally), so analyzing
    "the repository" and letting the engine fan out per-workflow matches
    how the analyzer is actually designed to be used.
    """

    __tablename__ = "analyses"

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")  # pending|completed|failed
    runs_analyzed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    analyzer_errors: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    repository: Mapped["Repository"] = relationship(back_populates="analyses")
    findings: Mapped[list["Finding"]] = relationship(back_populates="analysis", cascade="all, delete-orphan")
