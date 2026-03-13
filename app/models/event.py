import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, JSON, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Event(Base):
    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)  # Purchase, Lead, Custom
    event_data: Mapped[dict] = mapped_column(JSON, default={})
    user_data: Mapped[dict] = mapped_column(JSON, default={})  # email, phone (pre-hash)
    meta_response: Mapped[dict] = mapped_column(JSON, default={})
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending, sent, error
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
