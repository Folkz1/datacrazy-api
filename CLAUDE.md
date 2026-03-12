# DataCrazy API — Meta Pixel + CRM Reports

## O que é
Gateway de eventos CRM → Meta Conversions API + Relatórios inteligentes.
API-first: funciona com N8N, Lovable, curl, qualquer ferramenta.
Cliente: Alan (DataCrazy) — R$2.500 por etapas.

## Stack
- FastAPI + uvicorn
- PostgreSQL + asyncpg + SQLAlchemy async
- Auth: API key no header X-API-Key
- Meta Conversions API v21.0 via httpx
- Relatórios: Claude Sonnet via API

## Rodar
```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8100
# ou
docker-compose up
```

## Auth
- Master key: header `X-API-Key: dc-master-alan-2026`
- Client key: gerada ao criar client via POST /api/clients

## Endpoints
- POST/GET/PATCH/DELETE /api/clients (master key)
- POST /api/events/webhook (qualquer CRM → Meta CAPI)
- POST /api/events/track (disparo manual)
- GET /api/events (histórico)
- GET /api/events/stats (resumo)
- POST /api/reports/generate (relatório IA)
- GET /api/reports (lista)
- GET /api/reports/{id} (detalhe)
- GET /api/health
