"""Auto-sync CRM DataCrazy → Meta CAPI.

Polling: a cada 5 min, busca negócios que mudaram de stage e dispara eventos Meta.
Sem depender de webhooks do CRM — nós puxamos.
"""
import asyncio
import hashlib
import logging
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session
from app.models.client import Client
from app.models.event import Event
from app.services.datacrazy_service import DataCrazyClient
from app.services.meta_capi import send_event

logger = logging.getLogger("crm_sync")

# State: última checagem por client_id
_last_check: dict[str, str] = {}

# Sem mapeamento padrão — só dispara eventos para stages explicitamente configurados pelo cliente
DEFAULT_STAGE_MAP = {}

# Status → evento (quando negócio muda de status global)
STATUS_EVENT_MAP = {
    "won": "Purchase",
    "lost": None,  # não dispara
}


def _resolve_event_type(stage_name: str, status: str, client_stage_map: dict | None = None) -> str | None:
    """Resolve qual evento Meta disparar baseado no stage/status do deal."""
    # Status global tem prioridade
    if status == "won":
        return "Purchase"
    if status == "lost":
        return None

    # Mapa customizado do cliente (se configurado)
    if client_stage_map:
        for key, event in client_stage_map.items():
            if key.lower() in stage_name.lower():
                return event

    # Mapa padrão
    stage_lower = stage_name.lower().strip()
    for key, event in DEFAULT_STAGE_MAP.items():
        if key in stage_lower:
            return event

    return None


def _resolve_field(data: dict, field_path: str | None) -> str | None:
    """Resolve um campo pelo path configurado (ex: 'address.city', 'customFields.cep').
    Retorna None se path não existe ou valor vazio."""
    if not field_path or not data:
        return None
    parts = field_path.replace("[0]", ".0").split(".")
    current = data
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
        if current is None:
            return None
    return str(current).strip() if current else None


def _extract_contact(lead: dict) -> tuple[str | None, str | None]:
    """Extract email and phone from lead data."""
    email = lead.get("email") or None
    phone = lead.get("phone") or lead.get("rawPhone") or None

    for c in lead.get("contacts", []):
        platform = (c.get("platform") or c.get("type") or "").upper()
        value = c.get("value") or c.get("contactId") or c.get("rawValue")
        if platform in ("EMAIL",) and not email:
            email = value
        if platform in ("WHATSAPP", "PHONE", "MOBILE") and not phone:
            phone = value

    return email if email else None, phone if phone else None


async def sync_client(client: Client, stage_names: dict[str, str]) -> list[dict]:
    """Sync um cliente: busca deals que mudaram e dispara eventos.

    Args:
        client: Client do nosso sistema (com pixel_id, meta_access_token)
        stage_names: Mapa de stage_id → stage_name (pré-carregado)

    Returns:
        Lista de resultados [{event_type, lead_name, status, ...}]
    """
    dc = DataCrazyClient()
    if not dc.configured:
        return []

    client_id_str = str(client.id)
    now = datetime.now(timezone.utc).isoformat()

    # Buscar deals movidos desde última checagem
    last = _last_check.get(client_id_str)

    try:
        businesses = await dc.list_businesses(
            limit=100,
            last_moved_after=last,
        )
    except Exception as e:
        logger.error(f"[sync] Error fetching businesses: {e}")
        return []

    results = []

    for biz in businesses:
        stage_id = biz.get("stageId", "")
        stage_name = stage_names.get(stage_id, "")
        status = biz.get("status", "in_process")

        # Determinar evento
        custom_map = (client.crm_credentials or {}).get("stage_map")
        event_type = _resolve_event_type(stage_name, status, custom_map)
        if not event_type:
            continue

        # Verificar se evento está habilitado
        if event_type not in client.events_enabled:
            continue

        # Extrair dados do lead
        lead = biz.get("lead", {}) or {}
        email, phone = _extract_contact(lead)
        if not email and not phone:
            continue

        name = lead.get("name") or ""
        field_map = (client.crm_credentials or {}).get("field_map", {})
        user_data = {
            "email": email,
            "phone": phone,
            "first_name": name.split(" ")[0] or None,
            "last_name": " ".join(name.split(" ")[1:]) or None,
            "external_id": str(biz.get("leadId", "")),
            "city": _resolve_field(lead, field_map.get("city")) or lead.get("city") or lead.get("cidade") or None,
            "state": _resolve_field(lead, field_map.get("state")) or lead.get("state") or lead.get("estado") or lead.get("uf") or None,
            "country": _resolve_field(lead, field_map.get("country")) or lead.get("country") or "br",
            "zip_code": _resolve_field(lead, field_map.get("zip_code")) or lead.get("zipCode") or lead.get("cep") or None,
            "date_of_birth": _resolve_field(lead, field_map.get("date_of_birth")) or lead.get("birthDate") or lead.get("dateOfBirth") or None,
        }
        user_data = {k: v for k, v in user_data.items() if v}

        custom_data = {}
        if biz.get("total"):
            custom_data["value"] = float(biz["total"])
            custom_data["currency"] = "BRL"

        biz_id = biz.get("id", "")

        # Fan-out: disparar para CADA pixel ativo do cliente
        active_pixels = client.get_active_pixels()
        if not active_pixels:
            continue

        for pixel in active_pixels:
            px_id = pixel["pixel_id"]
            px_token = pixel["access_token"]
            px_label = pixel.get("label", "")

            # Dedup inclui pixel_id para não pular segundo pixel do mesmo deal
            dedup_key = f"{client.id}:{biz_id}:{event_type}:{px_id}"
            try:
                async with async_session() as db:
                    existing = await db.execute(
                        text("SELECT id FROM events WHERE client_id = :cid AND event_data->>'business_id' = :bid AND event_data->>'pixel_id' = :pid AND event_type = :etype AND status = 'sent' LIMIT 1"),
                        {"cid": str(client.id), "bid": str(biz_id), "pid": px_id, "etype": event_type}
                    )
                    if existing.scalar_one_or_none():
                        continue  # Já disparado para este pixel
            except Exception:
                pass

            deterministic_event_id = hashlib.sha256(dedup_key.encode()).hexdigest()[:32]

            try:
                meta_result = await send_event(
                    pixel_id=px_id,
                    access_token=px_token,
                    event_type=event_type,
                    user_data=user_data,
                    custom_data=custom_data or None,
                    event_id=deterministic_event_id,
                )

                try:
                    async with async_session() as db:
                        event = Event(
                            client_id=client.id,
                            event_type=event_type,
                            event_data={
                                "source": "crm_auto_sync",
                                "business_id": biz.get("id"),
                                "pixel_id": px_id,
                                "pixel_label": px_label,
                                "stage": stage_name,
                                "status": status,
                            },
                            user_data=user_data,
                            meta_response=meta_result.get("response", {}),
                            status="sent" if meta_result["success"] else "error",
                            error_message=meta_result.get("error"),
                        )
                        db.add(event)
                        await db.commit()
                except Exception as db_err:
                    logger.warning(f"[sync] DB save failed for {name} pixel {px_label} (client {client.id}): {db_err}")

                results.append({
                    "event_type": event_type,
                    "lead_name": name,
                    "stage": stage_name,
                    "pixel_id": px_id,
                    "pixel_label": px_label,
                    "status": "sent" if meta_result["success"] else "error",
                    "business_id": biz.get("id"),
                })

            except Exception as e:
                logger.error(f"[sync] Error firing event for {name} pixel {px_label}: {e}")

    _last_check[client_id_str] = now
    return results


async def run_sync_all() -> dict:
    """Roda sync para TODOS os clientes ativos. Chamado pelo cron."""
    dc = DataCrazyClient()
    if not dc.configured:
        return {"status": "skipped", "reason": "CRM not configured"}

    # Carregar todos os stage names (uma vez)
    stage_names = {}
    try:
        pipelines = await dc.list_pipelines()
        for p in pipelines:
            stages = await dc.get_pipeline_stages(str(p["id"]))
            for s in stages:
                stage_names[s["id"]] = s.get("name", "")
    except Exception as e:
        return {"status": "error", "reason": f"Failed to load pipelines: {e}"}

    # Buscar clientes ativos
    async with async_session() as db:
        result = await db.execute(select(Client).where(Client.active == True))
        active_clients = result.scalars().all()

    all_results = {}
    for client in active_clients:
        results = await sync_client(client, stage_names)
        if results:
            all_results[client.name] = results

    return {
        "status": "ok",
        "clients_checked": len(active_clients),
        "stages_loaded": len(stage_names),
        "events_fired": sum(len(r) for r in all_results.values()),
        "details": all_results,
    }


async def _cron_loop():
    """Loop infinito que roda sync a cada 5 minutos."""
    logger.info("[crm_sync] Cron started — polling every 5 minutes")
    while True:
        try:
            result = await run_sync_all()
            fired = result.get("events_fired", 0)
            if fired > 0:
                logger.info(f"[crm_sync] Fired {fired} events")
            else:
                logger.debug(f"[crm_sync] No new events")
        except Exception as e:
            logger.error(f"[crm_sync] Error: {e}")
        await asyncio.sleep(300)  # 5 minutos


def start_cron():
    """Inicia o cron em background. Chamar no lifespan do FastAPI."""
    asyncio.create_task(_cron_loop())
