from fastapi import Header, HTTPException, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.client import Client


async def get_api_key(x_api_key: str = Header(..., description="API key do cliente ou master key")):
    if not x_api_key:
        raise HTTPException(status_code=401, detail="API key required")
    return x_api_key


async def get_current_client(
    api_key: str = Depends(get_api_key),
    db: AsyncSession = Depends(get_db),
) -> Client | None:
    """Retorna o client associado à API key, ou None se for master key."""
    if api_key == settings.api_master_key:
        return None
    result = await db.execute(select(Client).where(Client.api_key == api_key, Client.active == True))
    client = result.scalar_one_or_none()
    if not client:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return client


async def require_master_key(api_key: str = Depends(get_api_key)):
    """Endpoints admin: só master key."""
    if api_key != settings.api_master_key:
        raise HTTPException(status_code=403, detail="Master key required")
    return api_key
