import uuid
from datetime import datetime, date

from sqlalchemy import String, DateTime, Date, Text, JSON, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    report_type: Mapped[str] = mapped_column(String(50), default="crm_weekly")
    raw_data: Mapped[dict] = mapped_column(JSON, default={})
    analysis: Mapped[str | None] = mapped_column(Text, nullable=True)
    metrics: Mapped[dict] = mapped_column(JSON, default={})
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending, generating, ready, error
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
