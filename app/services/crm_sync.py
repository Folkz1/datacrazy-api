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


def _resolve_event_type(stage_name: str, status: str, client_stage_map: dict | None = None) -> str | None:
    if status == "won":
        return "Purchase"
    if status == "lost":
        return None
    if client_stage_map:
        for key, event in client_stage_map.items():
            if key.lower() in stage_name.lower():
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
        stage_name = stage_names.get(stage_id, "")
        status = biz.get("status", "in_process")

        custom_map = (client.crm_credentials or {}).get("stage_map")
        event_type = _resolve_event_type(stage_name, status, custom_map)
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
                stages = await dc.get_pipeline_stages(str(p["id"]))
                for s in stages:
                    stage_names[s["id"]] = s.get("name", "")
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


def start_cron():
    """Inicia o cron em background."""
    asyncio.create_task(_cron_loop())
