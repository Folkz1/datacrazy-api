import uuid
from datetime import datetime

from sqlalchemy import String, Boolean, DateTime, JSON, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    pixel_id: Mapped[str] = mapped_column(String(100), nullable=False)
    meta_access_token: Mapped[str] = mapped_column(String(500), nullable=False)
    events_enabled: Mapped[list] = mapped_column(JSON, default=["Purchase", "Lead"])
    crm_credentials: Mapped[dict] = mapped_column(JSON, default={})
    # Multi-pixel: array of {"id","pixel_id","access_token","label","active"}
    pixels: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    api_key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def get_active_pixels(self) -> list[dict]:
        """Retorna pixels ativos. Fallback para pixel_id/meta_access_token legado."""
        if self.pixels:
            return [p for p in self.pixels if p.get("active", True)]
        # Backward compat: montar pixel a partir dos campos legados
        if self.pixel_id and self.meta_access_token:
            return [{"id": str(self.id), "pixel_id": self.pixel_id,
                     "access_token": self.meta_access_token, "label": "Principal", "active": True}]
        return []
