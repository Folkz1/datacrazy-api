# DataCrazy API - Meta Pixel + Google GA4 + CRM Reports

## What It Is
Gateway de eventos CRM -> Meta Conversions API + Google GA4 Measurement Protocol + relatorios inteligentes.
API-first: funciona com N8N, Lovable, curl e outras integracoes.
Cliente: Alan (DataCrazy) - projeto entregue por etapas.

## Stack
- FastAPI + uvicorn
- PostgreSQL + asyncpg + SQLAlchemy async
- Auth: API key no header `X-API-Key`
- Meta Conversions API v21.0 via httpx
- Relatorios via provedor LLM configurado por ambiente

## Rodar
```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8100
# ou
docker-compose up
```

## Auth
- Master key: header `X-API-Key: <API_MASTER_KEY>`
- Client key: gerada ao criar client via `POST /api/clients`

## Endpoints
- `POST/GET/PATCH/DELETE /api/clients` (master key)
- `POST /api/events/webhook` (qualquer CRM -> Meta CAPI)
- `POST /api/events/track` (disparo manual)
- `GET /api/events` (historico)
- `GET /api/events/stats` (resumo)
- `POST /api/reports/generate` (relatorio IA)
- `GET /api/reports` (lista)
- `GET /api/reports/{id}` (detalhe)
- `GET /api/health`
