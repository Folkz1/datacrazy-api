"""Google Analytics 4 — Measurement Protocol service.

Dispara eventos server-side para o Google Analytics (GA4).
Equivalente ao Meta CAPI, mas para o ecossistema Google.
Docs: https://developers.google.com/analytics/devguides/collection/protocol/ga4
"""
import hashlib
import uuid

import httpx

GA4_URL = "https://www.google-analytics.com/mp/collect"
GA4_DEBUG_URL = "https://www.google-analytics.com/debug/mp/collect"

# Mapeamento Meta → GA4 event names
META_TO_GA4_EVENT = {
    "Purchase": "purchase",
    "Lead": "generate_lead",
    "ViewContent": "view_item",
    "AddToCart": "add_to_cart",
    "InitiateCheckout": "begin_checkout",
    "CompleteRegistration": "sign_up",
}


def build_ga4_payload(
    event_type: str,
    user_data: dict,
    custom_data: dict | None = None,
    client_id: str | None = None,
    debug_mode: bool = False,
) -> dict:
    """Monta payload conforme spec GA4 Measurement Protocol."""
    # GA4 event name (lowercase)
    ga4_event = META_TO_GA4_EVENT.get(event_type, event_type.lower())

    # client_id: obrigatório no GA4 (ID do usuário, não do nosso client)
    # Gerar deterministicamente a partir de email ou phone
    if not client_id:
        seed = user_data.get("email") or user_data.get("phone") or str(uuid.uuid4())
        client_id = hashlib.md5(seed.encode()).hexdigest()[:16] + "." + str(int(__import__("time").time()))

    # User properties (hashed PII para match)
    user_properties = {}
    if user_data.get("email"):
        user_properties["email_sha256"] = {"value": hashlib.sha256(user_data["email"].strip().lower().encode()).hexdigest()}
    if user_data.get("phone"):
        digits = "".join(c for c in user_data["phone"] if c.isdigit())
        user_properties["phone_sha256"] = {"value": hashlib.sha256(digits.encode()).hexdigest()}

    # Event params
    params = {}
    if custom_data:
        if custom_data.get("value"):
            params["value"] = float(custom_data["value"])
            params["currency"] = custom_data.get("currency", "BRL")
        if custom_data.get("content_name"):
            params["item_name"] = custom_data["content_name"]

    # Engagement time — obrigatorio para eventos aparecerem no GA4 Realtime
    params["engagement_time_msec"] = "100"
    params["session_id"] = hashlib.md5((user_data.get("email") or user_data.get("phone") or "unknown").encode()).hexdigest()[:10]

    # User ID (external_id do CRM)
    user_id = None
    if user_data.get("external_id"):
        user_id = str(user_data["external_id"])

    if debug_mode:
        params["debug_mode"] = 1

    event = {"name": ga4_event, "params": params}

    payload = {
        "client_id": client_id,
        "events": [event],
    }
    if user_id:
        payload["user_id"] = user_id
    if user_properties:
        payload["user_properties"] = user_properties

    return payload


async def send_event(
    measurement_id: str,
    api_secret: str,
    event_type: str,
    user_data: dict,
    custom_data: dict | None = None,
    debug_mode: bool = False,
) -> dict:
    """Envia evento para Google Analytics 4 via Measurement Protocol.

    Returns:
        dict com 'success', 'response' ou 'error'
    """
    payload = build_ga4_payload(
        event_type=event_type,
        user_data=user_data,
        custom_data=custom_data,
        debug_mode=debug_mode,
    )

    url = GA4_DEBUG_URL if debug_mode else GA4_URL
    params = {
        "measurement_id": measurement_id,
        "api_secret": api_secret,
    }

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.post(url, params=params, json=payload)

            # GA4 retorna 204 No Content em sucesso (não-debug)
            # Debug retorna 200 com validação
            if resp.status_code in (200, 204):
                data = resp.json() if resp.content else {}
                # Debug mode retorna validation messages
                if debug_mode and data.get("validationMessages"):
                    errors = [m.get("description", "") for m in data["validationMessages"]]
                    if any(errors):
                        return {"success": False, "error": "; ".join(errors), "response": data}
                return {"success": True, "response": data, "measurement_id": measurement_id}
            else:
                return {"success": False, "error": f"HTTP {resp.status_code}", "status_code": resp.status_code}

        except httpx.RequestError as e:
            return {"success": False, "error": str(e)}
