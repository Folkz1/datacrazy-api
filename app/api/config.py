"""Config self-service — cliente configura pixels/GA4 pela interface."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_api_key, get_current_client
from app.core.database import get_db
from app.models.client import Client
from app.api.schemas import (
    ConfigResponse,
    ConfigUpdate,
    PixelEntry,
    GooglePixelEntry,
)

router = APIRouter(prefix="/api/config", tags=["Config (Self-Service)"])


@router.get("", response_model=ConfigResponse)
async def get_config(
    client: Client = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
):
    """Retorna config atual do cliente (pixels, GA4, eventos habilitados).
    Autenticado pela API key do client (header X-API-Key)."""
    if client is None:
        raise HTTPException(status_code=400, detail="Use client API key, not master key")
    return ConfigResponse(
        client_id=client.id,
        name=client.name,
        pixels=client.pixels or [],
        google_pixels=client.google_pixels or [],
        events_enabled=client.events_enabled or ["Purchase", "Lead"],
    )


@router.put("", response_model=ConfigResponse)
async def update_config(
    payload: ConfigUpdate,
    client: Client = Depends(get_current_client),
    db: AsyncSession = Depends(get_db),
):
    """Atualiza config do cliente. Só altera campos enviados.
    Autenticado pela API key do client (header X-API-Key)."""
    if client is None:
        raise HTTPException(status_code=400, detail="Use client API key, not master key")

    if payload.pixels is not None:
        client.pixels = [p.model_dump() for p in payload.pixels]
        # Sync legado com primeiro pixel ativo
        active = [p for p in payload.pixels if p.active]
        if active:
            client.pixel_id = active[0].pixel_id
            client.meta_access_token = active[0].access_token

    if payload.google_pixels is not None:
        client.google_pixels = [g.model_dump() for g in payload.google_pixels]

    if payload.events_enabled is not None:
        client.events_enabled = payload.events_enabled

    await db.commit()
    await db.refresh(client)

    return ConfigResponse(
        client_id=client.id,
        name=client.name,
        pixels=client.pixels or [],
        google_pixels=client.google_pixels or [],
        events_enabled=client.events_enabled or ["Purchase", "Lead"],
    )
