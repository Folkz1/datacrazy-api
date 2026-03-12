"""Serviço de relatórios inteligentes de CRM.

Gera análises automáticas dos últimos 7 dias usando Claude.
Quando tiver token DataCrazy: puxa dados reais.
Sem token: aceita dados via payload (flexível pra qualquer CRM).
"""
import json
from datetime import date, timedelta

import httpx

from app.core.config import settings


async def fetch_datacrazy_data(client_crm_creds: dict, period_days: int = 7) -> dict:
    """Puxa dados do DataCrazy API se token disponível."""
    token = client_crm_creds.get("datacrazy_token") or settings.datacrazy_api_token
    if not token:
        return {"source": "no_token", "data": None}

    base_url = client_crm_creds.get("datacrazy_url") or settings.datacrazy_api_url
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(timeout=30, headers=headers) as client:
        result = {"source": "datacrazy", "pipelines": [], "businesses": [], "leads_count": 0}

        try:
            # Pipelines e etapas
            resp = await client.get(f"{base_url}/api/v1/pipelines")
            if resp.status_code == 200:
                result["pipelines"] = resp.json()

            # Negócios recentes
            resp = await client.get(f"{base_url}/api/v1/businesses", params={"limit": 100})
            if resp.status_code == 200:
                result["businesses"] = resp.json()

            # Leads
            resp = await client.get(f"{base_url}/api/v1/leads", params={"limit": 50})
            if resp.status_code == 200:
                data = resp.json()
                result["leads_count"] = len(data) if isinstance(data, list) else data.get("total", 0)

        except httpx.RequestError:
            pass

        return result


async def generate_analysis(raw_data: dict, period_start: date, period_end: date) -> str:
    """Gera análise com Claude Sonnet."""
    if not settings.anthropic_api_key:
        return _fallback_analysis(raw_data, period_start, period_end)

    prompt = f"""Analise os dados de CRM do período {period_start} a {period_end} e gere um relatório executivo em português.

Dados:
{json.dumps(raw_data, ensure_ascii=False, default=str)[:8000]}

Formato do relatório:
## Resumo Executivo
(2-3 frases sobre o período)

## Métricas Principais
- Negócios novos:
- Negócios ganhos:
- Negócios perdidos:
- Taxa de conversão:
- Valor total pipeline:

## Destaques
(Pontos positivos)

## Alertas
(Problemas ou oportunidades perdidas)

## Recomendações
(3 ações práticas para a próxima semana)"""

    async with httpx.AsyncClient(timeout=60) as client:
        try:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": settings.anthropic_api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 2000,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                return data["content"][0]["text"]
        except httpx.RequestError:
            pass

    return _fallback_analysis(raw_data, period_start, period_end)


def _fallback_analysis(raw_data: dict, period_start: date, period_end: date) -> str:
    """Análise básica sem IA (fallback)."""
    businesses = raw_data.get("businesses", [])
    total = len(businesses) if isinstance(businesses, list) else 0
    return f"""## Relatório CRM — {period_start} a {period_end}

**Dados coletados:** {total} negócios no período.
**Fonte:** {raw_data.get('source', 'manual')}

> Análise automática com IA indisponível. Configure ANTHROPIC_API_KEY para relatórios inteligentes.

Dados brutos disponíveis via GET /api/reports/{{id}}."""
