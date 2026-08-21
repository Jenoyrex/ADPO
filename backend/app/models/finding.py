from __future__ import annotations

from sqlalchemy import JSON, Boolean, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class Finding(TimestampMixin, Base):
    """Persisted mirror of `adpo.models.Finding`.

    `adpo.models.Finding.recommendation` is a single string produced by the
    analyzer, not a separate normalized entity with its own lifecycle (no
    id, status, or assignment) - so there is deliberately no standalone
    `Recommendation` table here; that would model data the analyzer does
    not actually produce.
    """

    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[int] = mapped_column(
        ForeignKey("analyses.id", ondelete="CASCADE"), nullable=False, index=True
    )

    analyzer_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    confidence: Mapped[str] = mapped_column(String(16), nullable=False)
    recommendation: Mapped[str] = mapped_column(Text, nullable=False)

    estimated_savings_low_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    estimated_savings_high_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    estimated_savings_basis: Mapped[str | None] = mapped_column(String(512), nullable=True)
    estimated_savings_unknown: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    analysis: Mapped["Analysis"] = relationship(back_populates="findings")
