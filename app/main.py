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
from app.api import clients, events, reports, crm, config
from app.services.datacrazy_service import DataCrazyClient
from app.services.crm_sync import start_cron, run_sync_all, pause_cron, resume_cron, is_cron_paused, reset_last_check


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
app.include_router(config.router)


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
        "auto_sync": "paused" if is_cron_paused() else ("active" if settings.datacrazy_api_token else "disabled"),
        "meta_test_mode": bool(settings.meta_test_event_code),
        "google_ga4": True,
        "ai_reports": bool(settings.anthropic_api_key),
    }


@app.post("/api/sync", tags=["System"])
async def manual_sync(max_events: int = 0):
    """Força sync manual. max_events limita quantos eventos disparar (0 = usa config do client).
    Funciona mesmo com cron pausado."""
    result = await run_sync_all(max_events=max_events, force=True)
    return result


@app.post("/api/sync/pause", tags=["System"])
async def sync_pause():
    """Pausa o cron de sync automático (5 min). Sync manual ainda funciona."""
    pause_cron()
    return {"status": "paused", "message": "Cron auto-sync pausado. Use POST /api/sync para sync manual."}


@app.post("/api/sync/resume", tags=["System"])
async def sync_resume():
    """Retoma o cron de sync automático."""
    resume_cron()
    return {"status": "active", "message": "Cron auto-sync retomado (a cada 5 min)."}


@app.post("/api/sync/reset", tags=["System"])
async def sync_reset(client_id: str | None = None):
    """Reset do last_check — próximo sync re-processa todos os businesses."""
    reset_last_check(client_id)
    return {"status": "ok", "message": f"Last check resetado {'para ' + client_id if client_id else 'globalmente'}"}
