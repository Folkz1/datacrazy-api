"""Endpoints de eventos — webhook genérico + tracking manual + histórico."""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_api_key, get_current_client
from app.core.config import settings
from app.core.database import get_db
from app.models.client import Client
from app.models.event import Event
from app.api.schemas import EventTrack, WebhookPayload, EventResponse
from app.services.meta_capi import send_event as meta_send_event
from app.services.google_capi import send_event as google_send_event

router = APIRouter(prefix="/api/events", tags=["Events"])

# Mapeamento de eventos do CRM → Meta CAPI
CRM_EVENT_MAP = {
    # DataCrazy / genéricos
    "deal_won": "Purchase",
    "deal_closed": "Purchase",
    "negocio_ganho": "Purchase",
    "payment_confirmed": "Purchase",
    "pago": "Purchase",
    "lead_qualified": "Lead",
    "lead_qualificado": "Lead",
    "qualificado": "Lead",
    "new_lead": "Lead",
    "novo_lead": "Lead",
    # Passthrough direto
    "Purchase": "Purchase",
    "Lead": "Lead",
    "ViewContent": "ViewContent",
    "AddToCart": "AddToCart",
    "InitiateCheckout": "InitiateCheckout",
    "CompleteRegistration": "CompleteRegistration",
}


async def _resolve_client(db: AsyncSession, client_id: uuid.UUID | None, auth_client: Client | None) -> Client:
    """Resolve o client: ou vem da auth, ou pelo client_id."""
    if auth_client:
        return auth_client
    if client_id:
        result = await db.execute(select(Client).where(Client.id == client_id, Client.active == True))
        client = result.scalar_one_or_none()
        if client:
            return client
    raise HTTPException(status_code=400, detail="Client not found. Use client API key or pass client_id.")


@router.post("/webhook", response_model=EventResponse)
async def webhook_receiver(
    body: WebhookPayload,
    db: AsyncSession = Depends(get_db),
    api_key: str = Depends(get_api_key),
    auth_client: Client | None = Depends(get_current_client),
):
    """Webhook genérico — recebe evento de qualquer CRM e dispara pro Meta CAPI.

    Aceita eventos do DataCrazy, N8N, ou qualquer sistema.
    O campo 'event' é mapeado automaticamente para o tipo Meta (Purchase, Lead, etc).
    """
    # Resolver client
    if auth_client:
        client = auth_client
    elif body.client_identifier:
        result = await db.execute(
            select(Client).where(
                (Client.name == body.client_identifier) | (Client.pixel_id == body.client_identifier),
                Client.active == True,
            )
        )
        client = result.scalar_one_or_none()
        if not client:
            raise HTTPException(status_code=404, detail=f"Client '{body.client_identifier}' not found")
    else:
        raise HTTPException(status_code=400, detail="Provide client API key or client_identifier")

    # Mapear evento CRM → Meta
    meta_event = CRM_EVENT_MAP.get(body.event, body.event)
    if meta_event not in client.events_enabled:
        raise HTTPException(status_code=422, detail=f"Event '{meta_event}' not enabled for this client. Enabled: {client.events_enabled}")

    # Extrair user_data do payload
    data = body.data
    user_data = {
        "email": data.get("email"),
        "phone": data.get("phone") or data.get("telefone"),
        "first_name": data.get("first_name") or data.get("name", "").split(" ")[0] if data.get("name") else None,
        "last_name": data.get("last_name") or (" ".join(data.get("name", "").split(" ")[1:]) if data.get("name") else None),
        "external_id": data.get("deal_id") or data.get("lead_id") or data.get("id"),
        # Dados de localização (do CRM)
        "city": data.get("city") or data.get("cidade"),
        "state": data.get("state") or data.get("estado") or data.get("uf"),
        "country": data.get("country") or "br",
        "zip_code": data.get("zip_code") or data.get("cep") or data.get("postalCode"),
        "date_of_birth": data.get("date_of_birth") or data.get("birthDate") or data.get("dataNascimento"),
        # Dados de browser (de formulários web — melhoram match em até 32%)
        "fbc": data.get("fbc"),
        "fbp": data.get("fbp"),
        "client_ip_address": data.get("client_ip_address") or data.get("ip"),
        "client_user_agent": data.get("client_user_agent") or data.get("user_agent"),
        "fb_login_id": data.get("fb_login_id"),
    }

    # Custom data (valor, moeda, etc)
    custom_data = {}
    if data.get("value") or data.get("valor"):
        custom_data["value"] = float(data.get("value") or data.get("valor"))
        custom_data["currency"] = data.get("currency", "BRL")
    if data.get("content_name") or data.get("produto"):
        custom_data["content_name"] = data.get("content_name") or data.get("produto")

    # Fan-out: disparar para todos os pixels ativos (Meta + Google)
    active_pixels = client.get_active_pixels()
    active_google = client.get_active_google_pixels()
    if not active_pixels and not active_google:
        raise HTTPException(status_code=422, detail="Client has no active pixels configured (Meta or Google)")

    last_event = None

    # Meta CAPI
    for pixel in active_pixels:
        meta_result = await meta_send_event(
            pixel_id=pixel["pixel_id"],
            access_token=pixel["access_token"],
            event_type=meta_event,
            user_data=user_data,
            custom_data=custom_data or None,
        )

        event = Event(
            client_id=client.id,
            event_type=meta_event,
            event_data={"crm_event": body.event, "crm_data": body.data,
                        "platform": "meta",
                        "pixel_id": pixel["pixel_id"], "pixel_label": pixel.get("label", "")},
            user_data=user_data,
            meta_response=meta_result.get("response", {}),
            status="sent" if meta_result["success"] else "error",
            error_message=meta_result.get("error"),
        )
        db.add(event)
        last_event = event

    # Google GA4
    for gpixel in active_google:
        google_result = await google_send_event(
            measurement_id=gpixel["measurement_id"],
            api_secret=gpixel["api_secret"],
            event_type=meta_event,
            user_data=user_data,
            custom_data=custom_data or None,
        )

        event = Event(
            client_id=client.id,
            event_type=meta_event,
            event_data={"crm_event": body.event, "crm_data": body.data,
                        "platform": "google",
                        "measurement_id": gpixel["measurement_id"], "pixel_label": gpixel.get("label", "")},
            user_data=user_data,
            meta_response=google_result.get("response", {}),
            status="sent" if google_result["success"] else "error",
            error_message=google_result.get("error"),
        )
        db.add(event)
        last_event = event

    await db.commit()
    await db.refresh(last_event)
    return last_event


@router.post("/track", response_model=EventResponse)
async def track_event(
    body: EventTrack,
    db: AsyncSession = Depends(get_db),
    api_key: str = Depends(get_api_key),
    auth_client: Client | None = Depends(get_current_client),
):
    """Disparo manual de evento — controle total dos parâmetros."""
    client = await _resolve_client(db, body.client_id, auth_client)

    active_pixels = client.get_active_pixels()
    active_google = client.get_active_google_pixels()
    if not active_pixels and not active_google:
        raise HTTPException(status_code=422, detail="Client has no active pixels configured (Meta or Google)")

    last_event = None

    # Meta CAPI
    for pixel in active_pixels:
        meta_result = await meta_send_event(
            pixel_id=pixel["pixel_id"],
            access_token=pixel["access_token"],
            event_type=body.event_type,
            user_data=body.user_data,
            custom_data=body.custom_data,
            event_source_url=body.event_source_url,
            use_test_mode=body.test_mode,
        )

        event = Event(
            client_id=client.id,
            event_type=body.event_type,
            event_data={"source": "manual", "test_mode": body.test_mode,
                        "platform": "meta",
                        "pixel_id": pixel["pixel_id"], "pixel_label": pixel.get("label", "")},
            user_data=body.user_data,
            meta_response=meta_result.get("response", {}),
            status="sent" if meta_result["success"] else "error",
            error_message=meta_result.get("error"),
        )
        db.add(event)
        last_event = event

    # Google GA4
    for gpixel in active_google:
        google_result = await google_send_event(
            measurement_id=gpixel["measurement_id"],
            api_secret=gpixel["api_secret"],
            event_type=body.event_type,
            user_data=body.user_data,
            custom_data=body.custom_data,
            debug_mode=body.test_mode,
        )

        event = Event(
            client_id=client.id,
            event_type=body.event_type,
            event_data={"source": "manual", "test_mode": body.test_mode,
                        "platform": "google",
                        "measurement_id": gpixel["measurement_id"], "pixel_label": gpixel.get("label", "")},
            user_data=body.user_data,
            meta_response=google_result.get("response", {}),
            status="sent" if google_result["success"] else "error",
            error_message=google_result.get("error"),
        )
        db.add(event)
        last_event = event

    await db.commit()
    await db.refresh(last_event)
    return last_event


@router.get("", response_model=list[EventResponse])
async def list_events(
    client_id: uuid.UUID | None = Query(None),
    status: str | None = Query(None),
    event_type: str | None = Query(None),
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
    api_key: str = Depends(get_api_key),
    auth_client: Client | None = Depends(get_current_client),
):
    """Histórico de eventos com filtros."""
    query = select(Event).order_by(Event.created_at.desc()).limit(limit)

    if auth_client:
        query = query.where(Event.client_id == auth_client.id)
    elif client_id:
        query = query.where(Event.client_id == client_id)

    if status:
        query = query.where(Event.status == status)
    if event_type:
        query = query.where(Event.event_type == event_type)

    result = await db.execute(query)
    return result.scalars().all()


@router.get("/stats")
async def event_stats(
    client_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
    api_key: str = Depends(get_api_key),
    auth_client: Client | None = Depends(get_current_client),
):
    """Resumo de eventos por status e tipo."""
    filters = []
    if auth_client:
        filters.append(Event.client_id == auth_client.id)
    elif client_id:
        filters.append(Event.client_id == client_id)

    # Total por status
    q = select(Event.status, func.count(Event.id)).group_by(Event.status)
    if filters:
        q = q.where(and_(*filters))
    result = await db.execute(q)
    by_status = {row[0]: row[1] for row in result.all()}

    # Total por tipo
    q2 = select(Event.event_type, func.count(Event.id)).group_by(Event.event_type)
    if filters:
        q2 = q2.where(and_(*filters))
    result2 = await db.execute(q2)
    by_type = {row[0]: row[1] for row in result2.all()}

    return {
        "total": sum(by_status.values()),
        "by_status": by_status,
        "by_type": by_type,
    }
