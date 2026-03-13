"""Meta Conversions API (CAPI) service.

Dispara eventos server-side para o Meta Pixel.
Docs: https://developers.facebook.com/docs/marketing-api/conversions-api
"""
import hashlib
import time
import uuid

import httpx

from app.core.config import settings

META_GRAPH_URL = "https://graph.facebook.com/v21.0"


def hash_sha256(value: str | None) -> str | None:
    """Hash SHA-256 conforme exigência Meta CAPI (lowercase, trim, hash)."""
    if not value:
        return None
    normalized = value.strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def normalize_phone(phone: str | None) -> str | None:
    """Remove tudo que não é dígito, adiciona código país se necessário."""
    if not phone:
        return None
    digits = "".join(c for c in phone if c.isdigit())
    if not digits.startswith("55") and len(digits) <= 11:
        digits = "55" + digits
    return digits


def build_event_payload(
    event_type: str,
    user_data: dict,
    custom_data: dict | None = None,
    event_source_url: str | None = None,
    test_event_code: str | None = None,
) -> dict:
    """Monta payload conforme spec Meta CAPI."""
    # Hash dos dados do usuário (PII — Meta exige SHA-256)
    hashed_user = {}
    if user_data.get("email"):
        hashed_user["em"] = [hash_sha256(user_data["email"])]
    if user_data.get("phone"):
        phone = normalize_phone(user_data["phone"])
        hashed_user["ph"] = [hash_sha256(phone)]
    if user_data.get("first_name"):
        hashed_user["fn"] = [hash_sha256(user_data["first_name"])]
    if user_data.get("last_name"):
        hashed_user["ln"] = [hash_sha256(user_data["last_name"])]
    if user_data.get("city"):
        hashed_user["ct"] = [hash_sha256(user_data["city"])]
    if user_data.get("state"):
        hashed_user["st"] = [hash_sha256(user_data["state"])]
    if user_data.get("country"):
        hashed_user["country"] = [hash_sha256(user_data["country"])]
    if user_data.get("zip_code"):
        hashed_user["zp"] = [hash_sha256(user_data["zip_code"])]
    if user_data.get("date_of_birth"):
        hashed_user["db"] = [hash_sha256(user_data["date_of_birth"])]
    if user_data.get("external_id"):
        hashed_user["external_id"] = [hash_sha256(str(user_data["external_id"]))]

    # Parâmetros de browser — NÃO são hasheados (fbc, fbp, IP, User Agent)
    if user_data.get("fbc"):
        hashed_user["fbc"] = user_data["fbc"]
    if user_data.get("fbp"):
        hashed_user["fbp"] = user_data["fbp"]
    if user_data.get("client_ip_address"):
        hashed_user["client_ip_address"] = user_data["client_ip_address"]
    if user_data.get("client_user_agent"):
        hashed_user["client_user_agent"] = user_data["client_user_agent"]
    if user_data.get("fb_login_id"):
        hashed_user["fb_login_id"] = user_data["fb_login_id"]

    event = {
        "event_name": event_type,
        "event_time": int(time.time()),
        "event_id": str(uuid.uuid4()),
        "action_source": "system_generated",
        "user_data": hashed_user,
    }

    if event_source_url:
        event["event_source_url"] = event_source_url

    if custom_data:
        event["custom_data"] = custom_data

    payload = {"data": [event]}
    if test_event_code:
        payload["test_event_code"] = test_event_code

    return payload


async def send_event(
    pixel_id: str,
    access_token: str,
    event_type: str,
    user_data: dict,
    custom_data: dict | None = None,
    event_source_url: str | None = None,
    use_test_mode: bool = False,
) -> dict:
    """Envia evento para Meta Conversions API.

    Returns:
        dict com 'success', 'response' ou 'error'
    """
    test_code = settings.meta_test_event_code if use_test_mode else None

    payload = build_event_payload(
        event_type=event_type,
        user_data=user_data,
        custom_data=custom_data,
        event_source_url=event_source_url,
        test_event_code=test_code,
    )

    url = f"{META_GRAPH_URL}/{pixel_id}/events"
    params = {"access_token": access_token}

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.post(url, params=params, json=payload)
            data = resp.json()

            if resp.status_code == 200:
                return {"success": True, "response": data, "event_id": payload["data"][0]["event_id"]}
            else:
                return {"success": False, "error": data.get("error", {}).get("message", str(data)), "status_code": resp.status_code}

        except httpx.RequestError as e:
            return {"success": False, "error": str(e)}
