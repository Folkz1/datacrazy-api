"""CRUD de clientes — gestão multi-tenant."""
import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.auth import require_master_key
from app.core.database import get_db
from app.models.client import Client
from app.models.event import Event
from app.api.schemas import ClientCreate, ClientUpdate, ClientResponse

router = APIRouter(prefix="/api/clients", tags=["Clients"])


def generate_api_key() -> str:
    return f"dc_{secrets.token_hex(24)}"


@router.post("", response_model=ClientResponse, status_code=201)
async def create_client(
    body: ClientCreate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(require_master_key),
):
    # Build pixels array from either pixels[] or legacy pixel_id/meta_access_token
    pixels = [p.model_dump() for p in body.pixels] if body.pixels else []
    pixel_id = body.pixel_id
    meta_access_token = body.meta_access_token

    # If no pixels[] but legacy fields provided, create pixel from them
    if not pixels and pixel_id and meta_access_token:
        pixels = [{"id": str(uuid.uuid4()), "pixel_id": pixel_id,
                   "access_token": meta_access_token, "label": "Principal", "active": True}]

    # Keep legacy fields synced with first pixel for backward compat
    if pixels and not pixel_id:
        pixel_id = pixels[0]["pixel_id"]
        meta_access_token = pixels[0]["access_token"]

    client = Client(
        name=body.name,
        pixel_id=pixel_id or "",
        meta_access_token=meta_access_token or "",
        pixels=pixels,
        events_enabled=body.events_enabled,
        crm_credentials=body.crm_credentials,
        api_key=generate_api_key(),
    )
    db.add(client)
    await db.commit()
    await db.refresh(client)
    return client


@router.get("", response_model=list[ClientResponse])
async def list_clients(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(require_master_key),
):
    result = await db.execute(select(Client).order_by(Client.created_at.desc()))
    return result.scalars().all()


@router.get("/{client_id}", response_model=ClientResponse)
async def get_client(
    client_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(require_master_key),
):
    result = await db.execute(select(Client).where(Client.id == client_id))
    client = result.scalar_one_or_none()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    return client


@router.patch("/{client_id}", response_model=ClientResponse)
async def update_client(
    client_id: uuid.UUID,
    body: ClientUpdate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(require_master_key),
):
    result = await db.execute(select(Client).where(Client.id == client_id))
    client = result.scalar_one_or_none()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    update_data = body.model_dump(exclude_unset=True)

    # Handle pixels update — serialize PixelEntry objects and sync legacy fields
    if "pixels" in update_data and update_data["pixels"] is not None:
        pixels = update_data["pixels"]
        client.pixels = pixels
        flag_modified(client, "pixels")
        # Sync legacy fields with first active pixel
        active = [p for p in pixels if p.get("active", True)]
        if active:
            client.pixel_id = active[0]["pixel_id"]
            client.meta_access_token = active[0]["access_token"]
        del update_data["pixels"]

    for field, value in update_data.items():
        setattr(client, field, value)

    await db.commit()
    await db.refresh(client)
    return client


@router.delete("/{client_id}", status_code=204)
async def delete_client(
    client_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(require_master_key),
):
    result = await db.execute(select(Client).where(Client.id == client_id))
    client = result.scalar_one_or_none()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    await db.execute(delete(Event).where(Event.client_id == client_id))
    await db.delete(client)
    await db.commit()
