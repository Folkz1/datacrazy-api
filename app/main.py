"""DataCrazy API — Meta Pixel + CRM Reports

Gateway de eventos CRM → Meta Conversions API + Relatórios inteligentes.
API-first: funciona com N8N, Lovable, curl, qualquer ferramenta.

Docs: /docs (Swagger) | /redoc (ReDoc)
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.database import init_db
from app.core.config import settings
from app.api import clients, events, reports
from app.services.datacrazy_service import DataCrazyClient


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title="DataCrazy API — Meta Pixel + CRM Reports",
    description="Gateway de eventos CRM → Meta Conversions API. Multi-cliente, API-first.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(clients.router)
app.include_router(events.router)
app.include_router(reports.router)


@app.get("/api/health", tags=["System"])
async def health():
    dc = DataCrazyClient()
    dc_status = await dc.health_check()
    return {
        "status": "ok",
        "version": "1.0.0",
        "datacrazy_integration": dc_status,
        "meta_test_mode": bool(settings.meta_test_event_code),
        "ai_reports": bool(settings.anthropic_api_key),
    }
