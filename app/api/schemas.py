"""Schemas Pydantic para request/response da API."""
import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field


# === Clients ===

class PixelEntry(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="ID único do pixel")
    pixel_id: str = Field(..., description="Meta Pixel ID")
    access_token: str = Field(..., description="Meta Access Token (CAPI)")
    label: str = Field(default="Principal", description="Label (ex: B2B, Lançamento)")
    active: bool = Field(default=True, description="Pixel ativo ou inativo")


class ClientCreate(BaseModel):
    name: str = Field(..., description="Nome do cliente")
    pixel_id: str = Field("", description="Meta Pixel ID (legado, usar pixels[])")
    meta_access_token: str = Field("", description="Meta Access Token (legado, usar pixels[])")
    pixels: list[PixelEntry] = Field(default=[], description="Lista de pixels Meta (multi-pixel)")
    events_enabled: list[str] = Field(default=["Purchase", "Lead"], description="Tipos de evento habilitados")
    crm_credentials: dict = Field(default={}, description="Credenciais do CRM (ex: datacrazy_token)")


class ClientUpdate(BaseModel):
    name: str | None = None
    pixel_id: str | None = None
    meta_access_token: str | None = None
    pixels: list[PixelEntry] | None = None
    events_enabled: list[str] | None = None
    crm_credentials: dict | None = None
    active: bool | None = None


class ClientResponse(BaseModel):
    id: uuid.UUID
    name: str
    pixel_id: str
    meta_access_token: str
    pixels: list[dict] = []
    events_enabled: list[str]
    active: bool
    api_key: str
    created_at: datetime
    crm_credentials: dict = {}

    model_config = {"from_attributes": True}


# === Events ===

class EventTrack(BaseModel):
    client_id: uuid.UUID | None = Field(None, description="ID do cliente (opcional se auth por API key do cliente)")
    event_type: str = Field(..., description="Tipo do evento: Purchase, Lead, ViewContent, etc.")
    user_data: dict = Field(..., description="Dados do usuário: email, phone, first_name, last_name, city, state, country, external_id")
    custom_data: dict | None = Field(None, description="Dados customizados: value, currency, content_name, etc.")
    event_source_url: str | None = Field(None, description="URL de origem do evento")
    test_mode: bool = Field(False, description="Usar Meta Test Events")


class WebhookPayload(BaseModel):
    """Payload genérico de webhook — aceita qualquer CRM."""
    event: str = Field(..., description="Nome do evento no CRM (ex: deal_won, lead_qualified)")
    client_identifier: str | None = Field(None, description="Identificador do cliente (nome, ID, pixel)")
    data: dict = Field(default={}, description="Dados do evento (lead, deal, valores)")

    model_config = {"json_schema_extra": {
        "examples": [{
            "event": "deal_won",
            "client_identifier": "cliente-abc",
            "data": {
                "deal_id": "123",
                "value": 1500.00,
                "currency": "BRL",
                "email": "lead@example.com",
                "phone": "5547999220055",
                "name": "João Silva"
            }
        }]
    }}


class EventResponse(BaseModel):
    id: uuid.UUID
    client_id: uuid.UUID
    event_type: str
    status: str
    meta_response: dict
    error_message: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


# === Reports ===

class ReportGenerate(BaseModel):
    client_id: uuid.UUID
    period_days: int = Field(7, description="Período em dias (padrão: 7)")
    raw_data: dict | None = Field(None, description="Dados manuais (se não tiver integração CRM)")


class ReportResponse(BaseModel):
    id: uuid.UUID
    client_id: uuid.UUID
    period_start: date
    period_end: date
    report_type: str
    analysis: str | None
    metrics: dict
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}
