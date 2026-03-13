"""Endpoints CRM DataCrazy — visualizar dados e disparar eventos automaticamente."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_api_key, get_current_client, require_master_key
from app.core.database import get_db
from app.models.client import Client
from app.models.event import Event
from app.services.datacrazy_service import DataCrazyClient
from app.services.meta_capi import send_event

router = APIRouter(prefix="/api/crm", tags=["CRM DataCrazy"])


def _extract_contact(lead: dict) -> tuple[str | None, str | None]:
    """Extract email and phone from a lead, checking direct fields and contacts array."""
    email = lead.get("email") or None
    phone = lead.get("phone") or lead.get("rawPhone") or None

    # Also check contacts array (DataCrazy uses platform/contactId format)
    for c in lead.get("contacts", []):
        platform = (c.get("platform") or c.get("type") or "").upper()
        value = c.get("value") or c.get("contactId") or c.get("rawValue")
        if platform in ("EMAIL",) and not email:
            email = value
        if platform in ("WHATSAPP", "PHONE", "MOBILE") and not phone:
            phone = value

    return email if email else None, phone if phone else None


def _resolve_crm_field(data: dict, field_path: str | None) -> str | None:
    """Resolve campo do lead pelo path configurado (ex: 'address.city')."""
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


def _get_crm_client(token: str | None = None) -> DataCrazyClient:
    dc = DataCrazyClient(token=token)
    if not dc.configured:
        raise HTTPException(status_code=503, detail="DataCrazy API not configured (DATACRAZY_API_TOKEN missing)")
    return dc


@router.get("/pipelines")
async def list_pipelines(
    _: str = Depends(require_master_key),
):
    """Lista todos os pipelines do CRM com seus stages."""
    dc = _get_crm_client()
    pipelines = await dc.list_pipelines()
    result = []
    for p in pipelines:
        stages = await dc.get_pipeline_stages(str(p["id"]))
        result.append({
            "id": p["id"],
            "name": p.get("name", ""),
            "stages": [{"id": s["id"], "name": s.get("name", ""), "order": s.get("order", 0)} for s in stages],
            "stages_count": len(stages),
        })
    return result


@router.get("/leads")
async def list_leads(
    search: str | None = Query(None, description="Buscar por nome/email/telefone"),
    limit: int = Query(50, le=200),
    _: str = Depends(require_master_key),
):
    """Lista leads do CRM com dados de contato."""
    dc = _get_crm_client()
    leads = await dc.list_leads(limit=limit)
    result = []
    for lead in leads:
        email, phone = _extract_contact(lead)
        entry = {
            "id": lead["id"],
            "name": lead.get("name", ""),
            "email": email,
            "phone": phone,
            "tags": [t.get("name", "") for t in lead.get("tags", [])],
            "created_at": lead.get("createdAt"),
            "metrics": lead.get("metrics", {}),
        }

        # Filtro de busca simples
        if search:
            s = search.lower()
            match = (
                s in (entry["name"] or "").lower()
                or s in (entry["email"] or "").lower()
                or s in (entry["phone"] or "")
            )
            if not match:
                continue

        result.append(entry)

    return result


@router.get("/lead-fields")
async def get_lead_fields(
    _: str = Depends(require_master_key),
):
    """Retorna todos os campos disponíveis de um lead real do CRM (para configurar mapeamento)."""
    dc = _get_crm_client()
    leads = await dc.list_leads(limit=5)
    if not leads:
        return {"fields": [], "sample": {}}

    # Coletar todos os campos de múltiplos leads
    all_fields = {}
    for lead in leads:
        _collect_fields(lead, all_fields, prefix="")

    # Ordenar por frequência e retornar
    fields = sorted(all_fields.items(), key=lambda x: (-x[1]["count"], x[0]))
    return {
        "fields": [{"path": f[0], "sample_value": f[1]["sample"], "count": f[1]["count"]} for f in fields],
        "sample_lead": leads[0],
        "leads_analyzed": len(leads),
    }


def _collect_fields(obj: dict, result: dict, prefix: str):
    """Recursivamente coleta campos de um objeto, gerando paths tipo 'address.city'."""
    if not isinstance(obj, dict):
        return
    for key, value in obj.items():
        path = f"{prefix}{key}" if not prefix else f"{prefix}.{key}"
        if isinstance(value, dict):
            _collect_fields(value, result, path)
        elif isinstance(value, list):
            if value and isinstance(value[0], dict):
                _collect_fields(value[0], result, f"{path}[0]")
            elif value:
                if path not in result:
                    result[path] = {"sample": str(value[0])[:100], "count": 0}
                result[path]["count"] += 1
        else:
            if path not in result:
                result[path] = {"sample": str(value)[:100] if value else "", "count": 0}
            result[path]["count"] += 1


@router.get("/businesses")
async def list_businesses(
    pipeline_id: str | None = Query(None, description="Filtrar por pipeline"),
    stage_id: str | None = Query(None, description="Filtrar por stage"),
    limit: int = Query(50, le=200),
    _: str = Depends(require_master_key),
):
    """Lista negócios/deals do CRM."""
    dc = _get_crm_client()

    stage_ids = None
    if stage_id:
        stage_ids = [stage_id]
    elif pipeline_id:
        stages = await dc.get_pipeline_stages(pipeline_id)
        stage_ids = [str(s["id"]) for s in stages]

    businesses = await dc.list_businesses(stage_ids=stage_ids, limit=limit)
    result = []
    for b in businesses:
        lead = b.get("lead", {}) or {}
        email, phone = _extract_contact(lead)

        result.append({
            "id": b["id"],
            "code": b.get("code"),
            "status": b.get("status"),
            "stage_id": b.get("stageId"),
            "total": b.get("total"),
            "lead_id": b.get("leadId"),
            "lead_name": lead.get("name") if lead else None,
            "lead_email": email,
            "lead_phone": phone,
            "last_moved_at": b.get("lastMovedAt"),
            "created_at": b.get("createdAt"),
            "products": b.get("products", []),
        })
    return result


@router.post("/fire-event")
async def fire_event_from_crm(
    client_id: uuid.UUID = Query(..., description="ID do cliente (nosso sistema)"),
    lead_id: str | None = Query(None, description="ID do lead no CRM DataCrazy"),
    business_id: str | None = Query(None, description="ID do negócio no CRM DataCrazy"),
    event_type: str = Query("Lead", description="Tipo do evento Meta: Lead, Purchase, etc."),
    test_mode: bool = Query(True, description="Usar modo teste Meta"),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(require_master_key),
):
    """Puxa dados de um lead/negócio do CRM e dispara evento Meta CAPI automaticamente.

    Fluxo: CRM DataCrazy → extrai dados → hasheia → envia Meta CAPI
    """
    # Resolver client do nosso sistema
    result = await db.execute(select(Client).where(Client.id == client_id, Client.active == True))
    client = result.scalar_one_or_none()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    if event_type not in client.events_enabled:
        raise HTTPException(status_code=422, detail=f"Event '{event_type}' not enabled. Enabled: {client.events_enabled}")

    dc = _get_crm_client()

    # Buscar dados do CRM
    user_data = {}
    custom_data = {}
    crm_source = {}

    if business_id:
        biz = await dc.get_business(business_id)
        crm_source = {"type": "business", "id": business_id, "data": biz}

        lead = biz.get("lead", {}) or {}
        if lead:
            name = lead.get("name") or ""
            user_data["first_name"] = name.split(" ")[0] or None
            user_data["last_name"] = " ".join(name.split(" ")[1:]) or None
            user_data["external_id"] = str(biz.get("leadId", ""))
            email, phone = _extract_contact(lead)
            user_data["email"] = email
            user_data["phone"] = phone
            field_map = (client.crm_credentials or {}).get("field_map", {})
            user_data["city"] = _resolve_crm_field(lead, field_map.get("city")) or lead.get("city") or lead.get("cidade") or None
            user_data["state"] = _resolve_crm_field(lead, field_map.get("state")) or lead.get("state") or lead.get("estado") or lead.get("uf") or None
            user_data["country"] = _resolve_crm_field(lead, field_map.get("country")) or lead.get("country") or "br"
            user_data["zip_code"] = _resolve_crm_field(lead, field_map.get("zip_code")) or lead.get("zipCode") or lead.get("cep") or None
            user_data["date_of_birth"] = _resolve_crm_field(lead, field_map.get("date_of_birth")) or lead.get("birthDate") or lead.get("dateOfBirth") or None

        if biz.get("total"):
            custom_data["value"] = float(biz["total"])
            custom_data["currency"] = "BRL"

        products = biz.get("products", [])
        if products:
            custom_data["content_name"] = products[0].get("name", "")

    elif lead_id:
        lead = await dc.get_lead(lead_id)
        crm_source = {"type": "lead", "id": lead_id, "data": lead}

        name = lead.get("name") or ""
        user_data["first_name"] = name.split(" ")[0] or None
        user_data["last_name"] = " ".join(name.split(" ")[1:]) or None
        user_data["external_id"] = str(lead_id)
        email, phone = _extract_contact(lead)
        user_data["email"] = email
        user_data["phone"] = phone

        metrics = lead.get("metrics", {})
        if metrics.get("totalSpent"):
            custom_data["value"] = float(metrics["totalSpent"])
            custom_data["currency"] = "BRL"
    else:
        raise HTTPException(status_code=400, detail="Provide lead_id or business_id")

    # Limpar None values
    user_data = {k: v for k, v in user_data.items() if v}

    if not user_data.get("email") and not user_data.get("phone"):
        raise HTTPException(
            status_code=422,
            detail="Lead/Business has no email or phone — cannot match on Meta. Add contact data in the CRM first."
        )

    # Disparar pra Meta
    meta_result = await send_event(
        pixel_id=client.pixel_id,
        access_token=client.meta_access_token,
        event_type=event_type,
        user_data=user_data,
        custom_data=custom_data or None,
        use_test_mode=test_mode,
    )

    # Salvar log
    event = Event(
        client_id=client.id,
        event_type=event_type,
        event_data={"source": "crm_auto", "crm": crm_source, "test_mode": test_mode},
        user_data=user_data,
        meta_response=meta_result.get("response", {}),
        status="sent" if meta_result["success"] else "error",
        error_message=meta_result.get("error"),
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)

    return {
        "event_id": str(event.id),
        "status": event.status,
        "event_type": event_type,
        "user_data_extracted": user_data,
        "custom_data": custom_data,
        "meta_response": meta_result.get("response", {}),
        "error": meta_result.get("error"),
        "crm_source": {"type": crm_source.get("type"), "id": crm_source.get("id")},
    }
