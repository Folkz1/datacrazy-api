"""Auto-sync CRM DataCrazy → Meta CAPI.

Polling controlável: sync_enabled per-client, max_events limit, pause/resume global.
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
from app.services.meta_capi import send_event as meta_send_event
from app.services.google_capi import send_event as google_send_event

logger = logging.getLogger("crm_sync")

# State
_last_check: dict[str, str] = {}
_cron_paused = False  # Global pause flag

DEFAULT_STAGE_MAP = {}

STATUS_EVENT_MAP = {
    "won": "Purchase",
    "lost": None,
}

# --- Control API ---

def is_cron_paused() -> bool:
    return _cron_paused

def pause_cron():
    global _cron_paused
    _cron_paused = True

def resume_cron():
    global _cron_paused
    _cron_paused = False

def reset_last_check(client_id: str | None = None):
    """Reset last_check to re-process all businesses."""
    if client_id:
        _last_check.pop(client_id, None)
    else:
        _last_check.clear()


def _resolve_event_type(stage_name: str, status: str, client_stage_map: dict | None = None, stage_id: str | None = None) -> str | None:
    if status == "won":
        return "Purchase"
    if status == "lost":
        return None
    if client_stage_map:
        # New format: stage_map keyed by stage_id (exact match, no ambiguity)
        if stage_id and stage_id in client_stage_map:
            entry = client_stage_map[stage_id]
            if isinstance(entry, dict):
                return entry.get("event")
            return entry
        # Legacy format: name-based substring match (backward compat)
        for key, event in client_stage_map.items():
            if isinstance(event, str) and key.lower() in stage_name.lower():
                return event
    stage_lower = stage_name.lower().strip()
    for key, event in DEFAULT_STAGE_MAP.items():
        if key in stage_lower:
            return event
    return None


def _resolve_field(data: dict, field_path: str | None) -> str | None:
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


async def sync_client(client: Client, stage_names: dict[str, str], max_events: int = 0) -> list[dict]:
    """Sync um cliente. max_events=0 means unlimited."""
    # Check per-client sync settings (default: enabled if client has stage_map)
    sync_settings = (client.crm_credentials or {}).get("sync_settings", {})
    has_stage_map = bool((client.crm_credentials or {}).get("stage_map"))
    if not sync_settings.get("sync_enabled", has_stage_map):
        return []

    client_max = sync_settings.get("sync_max_events", 10)
    effective_max = max_events if max_events > 0 else client_max

    client_token = (client.crm_credentials or {}).get("datacrazy_token")
    dc = DataCrazyClient(token=client_token)
    if not dc.configured:
        return []

    client_id_str = str(client.id)
    now = datetime.now(timezone.utc).isoformat()
    last = _last_check.get(client_id_str)

    try:
        businesses = await dc.list_businesses(
            limit=100,
            last_moved_after=last,
        )
    except Exception as e:
        logger.error(f"[sync] Error fetching businesses for {client.name}: {e}")
        return []

    results = []

    for biz in businesses:
        # Check limit
        if effective_max > 0 and len(results) >= effective_max:
            logger.info(f"[sync] Hit max_events limit ({effective_max}) for {client.name}")
            break

        stage_id = biz.get("stageId", "")
        stage_info = stage_names.get(stage_id, {})
        stage_name = stage_info.get("name", "") if isinstance(stage_info, dict) else stage_info
        status = biz.get("status", "in_process")

        custom_map = (client.crm_credentials or {}).get("stage_map")
        event_type = _resolve_event_type(stage_name, status, custom_map, stage_id=stage_id)
        if not event_type:
            continue

        if event_type not in client.events_enabled:
            continue

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

        active_pixels = client.get_active_pixels()
        active_google = client.get_active_google_pixels()
        if not active_pixels and not active_google:
            continue

        # --- Meta CAPI ---
        for pixel in active_pixels:
            if effective_max > 0 and len(results) >= effective_max:
                break

            px_id = pixel["pixel_id"]
            px_token = pixel["access_token"]
            px_label = pixel.get("label", "")

            # Dedup: skip if already sent OR if error was auth/token (no point retrying)
            dedup_key = f"{client.id}:{biz_id}:{event_type}:{px_id}"
            try:
                async with async_session() as db:
                    existing = await db.execute(
                        text("""SELECT id, status, error_message FROM events
                               WHERE client_id = :cid
                               AND event_data->>'business_id' = :bid
                               AND event_type = :etype
                               AND (event_data->>'pixel_id' = :pid OR event_data->>'pixel_id' IS NULL)
                               LIMIT 1"""),
                        {"cid": str(client.id), "bid": str(biz_id), "pid": px_id, "etype": event_type}
                    )
                    row = existing.first()
                    if row:
                        # Skip if sent successfully
                        if row.status == "sent":
                            continue
                        # Skip if error was auth/token (don't retry until token renewed)
                        err_msg = row.error_message or ""
                        if "access token" in err_msg.lower() or "session has expired" in err_msg.lower():
                            continue
            except Exception:
                pass

            deterministic_event_id = hashlib.sha256(dedup_key.encode()).hexdigest()[:32]

            try:
                meta_result = await meta_send_event(
                    pixel_id=px_id,
                    access_token=px_token,
                    event_type=event_type,
                    user_data=user_data,
                    custom_data=custom_data or None,
                    event_id=deterministic_event_id,
                )

                try:
                    async with async_session() as db:
                        pipeline_id = stage_info.get("pipeline_id", "") if isinstance(stage_info, dict) else ""
                        event = Event(
                            client_id=client.id,
                            event_type=event_type,
                            event_data={
                                "source": "crm_auto_sync",
                                "business_id": biz.get("id"),
                                "pixel_id": px_id,
                                "pixel_label": px_label,
                                "stage": stage_name,
                                "stage_id": stage_id,
                                "pipeline_id": pipeline_id,
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
                    logger.warning(f"[sync] DB save failed for {name} pixel {px_label}: {db_err}")

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

        # --- Google GA4 ---
        for gpixel in active_google:
            if effective_max > 0 and len(results) >= effective_max:
                break

            gp_mid = gpixel["measurement_id"]
            gp_secret = gpixel["api_secret"]
            gp_label = gpixel.get("label", "")

            dedup_key = f"{client.id}:{biz_id}:{event_type}:google:{gp_mid}"
            try:
                async with async_session() as db:
                    existing = await db.execute(
                        text("""SELECT id, status, error_message FROM events
                               WHERE client_id = :cid
                               AND event_data->>'business_id' = :bid
                               AND event_type = :etype
                               AND event_data->>'platform' = 'google'
                               AND event_data->>'measurement_id' = :mid
                               LIMIT 1"""),
                        {"cid": str(client.id), "bid": str(biz_id), "mid": gp_mid, "etype": event_type}
                    )
                    row = existing.first()
                    if row:
                        if row.status == "sent":
                            continue
                        err_msg = row.error_message or ""
                        if "access token" in err_msg.lower() or "session has expired" in err_msg.lower():
                            continue
            except Exception:
                pass

            try:
                google_result = await google_send_event(
                    measurement_id=gp_mid,
                    api_secret=gp_secret,
                    event_type=event_type,
                    user_data=user_data,
                    custom_data=custom_data or None,
                )

                try:
                    async with async_session() as db:
                        pipeline_id = stage_info.get("pipeline_id", "") if isinstance(stage_info, dict) else ""
                        event = Event(
                            client_id=client.id,
                            event_type=event_type,
                            event_data={
                                "source": "crm_auto_sync",
                                "business_id": biz.get("id"),
                                "platform": "google",
                                "measurement_id": gp_mid,
                                "pixel_label": gp_label,
                                "stage": stage_name,
                                "stage_id": stage_id,
                                "pipeline_id": pipeline_id,
                                "status": status,
                            },
                            user_data=user_data,
                            meta_response=google_result.get("response", {}),
                            status="sent" if google_result["success"] else "error",
                            error_message=google_result.get("error"),
                        )
                        db.add(event)
                        await db.commit()
                except Exception as db_err:
                    logger.warning(f"[sync] DB save failed for {name} google {gp_label}: {db_err}")

                results.append({
                    "event_type": event_type,
                    "lead_name": name,
                    "stage": stage_name,
                    "platform": "google",
                    "measurement_id": gp_mid,
                    "pixel_label": gp_label,
                    "status": "sent" if google_result["success"] else "error",
                    "business_id": biz.get("id"),
                })

            except Exception as e:
                logger.error(f"[sync] Error firing Google event for {name} pixel {gp_label}: {e}")

    _last_check[client_id_str] = now
    return results


async def run_sync_all(max_events: int = 0, force: bool = False) -> dict:
    """Roda sync para TODOS os clientes ativos. force=True ignora pause (manual sync)."""
    if _cron_paused and not force:
        return {"status": "paused", "reason": "Cron is paused globally"}

    async with async_session() as db:
        result = await db.execute(select(Client).where(Client.active == True))
        active_clients = result.scalars().all()

    if not active_clients:
        return {"status": "skipped", "reason": "No active clients"}

    all_results = {}
    total_stages = 0
    for client in active_clients:
        client_token = (client.crm_credentials or {}).get("datacrazy_token")
        dc = DataCrazyClient(token=client_token)
        if not dc.configured:
            continue

        stage_names = {}
        try:
            pipelines = await dc.list_pipelines()
            for p in pipelines:
                pipeline_id = str(p["id"])
                stages = await dc.get_pipeline_stages(pipeline_id)
                for s in stages:
                    stage_names[s["id"]] = {"name": s.get("name", ""), "pipeline_id": pipeline_id}
            total_stages = max(total_stages, len(stage_names))
        except Exception as e:
            logger.error(f"[sync] Failed to load pipelines for {client.name}: {e}")
            continue

        results = await sync_client(client, stage_names, max_events=max_events)
        if results:
            all_results[client.name] = results

    return {
        "status": "ok",
        "clients_checked": len(active_clients),
        "stages_loaded": total_stages,
        "events_fired": sum(len(r) for r in all_results.values()),
        "details": all_results,
    }


async def _cron_loop():
    """Loop que roda sync a cada 5 minutos (respects pause)."""
    logger.info("[crm_sync] Cron started — polling every 5 minutes")
    while True:
        if not _cron_paused:
            try:
                result = await run_sync_all()
                fired = result.get("events_fired", 0)
                if fired > 0:
                    logger.info(f"[crm_sync] Fired {fired} events")
            except Exception as e:
                logger.error(f"[crm_sync] Error: {e}")
        await asyncio.sleep(300)


# --- Full Sync (historical) ---

_full_sync_status: dict[str, dict] = {}  # client_id -> {status, processed, total, errors, started_at}


def get_full_sync_status(client_id: str | None = None) -> dict:
    if client_id:
        return _full_sync_status.get(client_id, {"status": "idle"})
    return _full_sync_status


async def run_full_sync(client_id: str):
    """Sync completo do histórico — pagina todos os deals, processa em background.
    Rate limit: 10 páginas por minuto (1 a cada 6s)."""
    from app.models.client import Client

    async with async_session() as db:
        result = await db.execute(select(Client).where(Client.id == client_id, Client.active == True))
        client = result.scalar_one_or_none()
        if not client:
            _full_sync_status[client_id] = {"status": "error", "message": "Client not found"}
            return

    client_token = (client.crm_credentials or {}).get("datacrazy_token")
    dc = DataCrazyClient(token=client_token)
    if not dc.configured:
        _full_sync_status[client_id] = {"status": "error", "message": "CRM token not configured"}
        return

    # Load stage names
    stage_names = {}
    try:
        pipelines = await dc.list_pipelines()
        for p in pipelines:
            pipeline_id = str(p["id"])
            stages = await dc.get_pipeline_stages(pipeline_id)
            for s in stages:
                stage_names[s["id"]] = {"name": s.get("name", ""), "pipeline_id": pipeline_id}
    except Exception as e:
        _full_sync_status[client_id] = {"status": "error", "message": f"Failed to load pipelines: {e}"}
        return

    # Get total count
    try:
        first_page = await dc.list_businesses(limit=1)
    except Exception:
        first_page = []

    # Estimate total from CRM
    total_estimate = 0
    try:
        import httpx
        async with httpx.AsyncClient(timeout=30, headers={"Authorization": f"Bearer {client_token}"}) as http:
            resp = await http.get(f"{dc.base_url}/api/v1/businesses", params={"take": 1})
            raw = resp.json()
            total_estimate = raw.get("count", 0)
    except Exception:
        total_estimate = 0

    _full_sync_status[client_id] = {
        "status": "running",
        "processed": 0,
        "fired": 0,
        "skipped": 0,
        "total": total_estimate,
        "errors": 0,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }

    custom_map = (client.crm_credentials or {}).get("stage_map")
    if not custom_map:
        _full_sync_status[client_id] = {"status": "error", "message": "No stage_map configured"}
        return

    skip = 0
    page_size = 100
    pages_this_minute = 0

    while True:
        # Rate limit: 10 pages per minute
        if pages_this_minute >= 10:
            await asyncio.sleep(60)
            pages_this_minute = 0

        try:
            businesses = await dc.list_businesses(limit=page_size, skip=skip)
        except Exception as e:
            _full_sync_status[client_id]["errors"] += 1
            logger.error(f"[full_sync] Error fetching page skip={skip}: {e}")
            await asyncio.sleep(6)
            pages_this_minute += 1
            skip += page_size
            continue

        if not businesses:
            break

        pages_this_minute += 1

        for biz in businesses:
            _full_sync_status[client_id]["processed"] += 1

            stage_id = biz.get("stageId", "")
            stage_info = stage_names.get(stage_id, {})
            stage_name = stage_info.get("name", "") if isinstance(stage_info, dict) else stage_info
            status = biz.get("status", "in_process")

            event_type = _resolve_event_type(stage_name, status, custom_map, stage_id=stage_id)
            if not event_type:
                _full_sync_status[client_id]["skipped"] += 1
                continue

            if event_type not in client.events_enabled:
                _full_sync_status[client_id]["skipped"] += 1
                continue

            lead = biz.get("lead", {}) or {}
            email, phone = _extract_contact(lead)
            if not email and not phone:
                _full_sync_status[client_id]["skipped"] += 1
                continue

            biz_id = biz.get("id", "")
            active_pixels = client.get_active_pixels()

            for pixel in active_pixels:
                px_id = pixel["pixel_id"]
                px_token = pixel["access_token"]

                # Dedup
                try:
                    async with async_session() as db:
                        existing = await db.execute(
                            text("""SELECT id, status, error_message FROM events
                                   WHERE client_id = :cid
                                   AND event_data->>'business_id' = :bid
                                   AND event_type = :etype
                                   AND (event_data->>'pixel_id' = :pid OR event_data->>'pixel_id' IS NULL)
                                   LIMIT 1"""),
                            {"cid": str(client.id), "bid": str(biz_id), "pid": px_id, "etype": event_type}
                        )
                        row = existing.first()
                        if row:
                            if row.status == "sent":
                                continue
                            err_msg = row.error_message or ""
                            if "access token" in err_msg.lower() or "session has expired" in err_msg.lower():
                                continue
                except Exception:
                    pass

                name = lead.get("name") or ""
                field_map = (client.crm_credentials or {}).get("field_map", {})
                user_data = {
                    "email": email,
                    "phone": phone,
                    "first_name": name.split(" ")[0] or None,
                    "last_name": " ".join(name.split(" ")[1:]) or None,
                    "external_id": str(biz.get("leadId", "")),
                    "country": "br",
                }
                user_data = {k: v for k, v in user_data.items() if v}

                custom_data = {}
                if biz.get("total"):
                    custom_data["value"] = float(biz["total"])
                    custom_data["currency"] = "BRL"

                dedup_key = f"{client.id}:{biz_id}:{event_type}:{px_id}"
                deterministic_event_id = hashlib.sha256(dedup_key.encode()).hexdigest()[:32]

                try:
                    meta_result = await meta_send_event(
                        pixel_id=px_id,
                        access_token=px_token,
                        event_type=event_type,
                        user_data=user_data,
                        custom_data=custom_data or None,
                        event_id=deterministic_event_id,
                    )

                    async with async_session() as db:
                        event = Event(
                            client_id=client.id,
                            event_type=event_type,
                            event_data={
                                "source": "full_sync",
                                "business_id": biz_id,
                                "pixel_id": px_id,
                                "pixel_label": pixel.get("label", ""),
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

                    if meta_result["success"]:
                        _full_sync_status[client_id]["fired"] += 1
                    else:
                        _full_sync_status[client_id]["errors"] += 1

                except Exception as e:
                    _full_sync_status[client_id]["errors"] += 1
                    logger.error(f"[full_sync] Error firing for {name}: {e}")

        skip += page_size
        if skip >= total_estimate or len(businesses) < page_size:
            break

        # Rate limit delay
        await asyncio.sleep(6)

    _full_sync_status[client_id]["status"] = "completed"
    _full_sync_status[client_id]["completed_at"] = datetime.now(timezone.utc).isoformat()
    logger.info(f"[full_sync] Completed for {client.name}: {_full_sync_status[client_id]}")

    # Mark full_sync_completed in client crm_credentials
    try:
        async with async_session() as db:
            result = await db.execute(select(Client).where(Client.id == client_id))
            c = result.scalar_one_or_none()
            if c:
                creds = dict(c.crm_credentials or {})
                ss = dict(creds.get("sync_settings", {}))
                ss["full_sync_completed"] = True
                ss["full_sync_completed_at"] = datetime.now(timezone.utc).isoformat()
                creds["sync_settings"] = ss
                c.crm_credentials = creds
                await db.commit()
    except Exception as e:
        logger.warning(f"[full_sync] Failed to mark completed: {e}")


def start_cron():
    """Inicia o cron em background."""
    asyncio.create_task(_cron_loop())
