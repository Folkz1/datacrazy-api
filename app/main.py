"""DataCrazy API — Meta Pixel + CRM Reports

Gateway de eventos CRM → Meta Conversions API + Relatórios inteligentes.
API-first: funciona com N8N, Lovable, curl, qualquer ferramenta.

Docs: /docs (Swagger) | /redoc (ReDoc)
"""
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.core.database import init_db
from app.core.config import settings
from app.api import clients, events, reports, crm
from app.services.datacrazy_service import DataCrazyClient
from app.services.crm_sync import start_cron, run_sync_all


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    if settings.datacrazy_api_token:
        start_cron()
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
app.include_router(crm.router)


# Dashboard static files
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
async def dashboard():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/health", tags=["System"])
async def health():
    dc = DataCrazyClient()
    dc_status = await dc.health_check()
    return {
        "status": "ok",
        "version": "1.2.0",
        "datacrazy_integration": dc_status,
        "auto_sync": "active" if settings.datacrazy_api_token else "disabled",
        "meta_test_mode": bool(settings.meta_test_event_code),
        "google_ga4": True,
        "ai_reports": bool(settings.anthropic_api_key),
    }


@app.post("/api/sync", tags=["System"])
async def manual_sync():
    """Força sync manual — busca mudanças no CRM e dispara eventos Meta."""
    result = await run_sync_all()
    return result
