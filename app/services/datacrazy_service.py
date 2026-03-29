"""DataCrazy CRM API client.

Integração com https://api.g1.datacrazy.io
Usado para polling de eventos e dados de leads/negócios.
"""
import httpx

from app.core.config import settings


class DataCrazyClient:
    """Client assíncrono para a API do DataCrazy."""

    def __init__(self, token: str | None = None, base_url: str | None = None):
        self.token = token or settings.datacrazy_api_token
        self.base_url = (base_url or settings.datacrazy_api_url).rstrip("/")
        self.headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}

    @property
    def configured(self) -> bool:
        return bool(self.token)

    def _extract_data(self, response_json):
        """Extract data from API response — handles both {count, data} and raw formats."""
        if isinstance(response_json, dict) and "data" in response_json:
            return response_json["data"]
        return response_json

    async def list_pipelines(self) -> list:
        async with httpx.AsyncClient(timeout=30, headers=self.headers) as client:
            resp = await client.get(f"{self.base_url}/api/v1/pipelines")
            resp.raise_for_status()
            return self._extract_data(resp.json())

    async def get_pipeline_stages(self, pipeline_id: str) -> list:
        async with httpx.AsyncClient(timeout=30, headers=self.headers) as client:
            resp = await client.get(f"{self.base_url}/api/v1/pipelines/{pipeline_id}/stages")
            resp.raise_for_status()
            return self._extract_data(resp.json())

    async def list_businesses(
        self,
        stage_ids: list[str] | None = None,
        limit: int = 100,
        last_moved_after: str | None = None,
        status: str | None = None,
    ) -> list:
        params: dict = {"take": limit}
        if stage_ids:
            params["filter[stageId]"] = ",".join(stage_ids)
        if last_moved_after:
            params["filter[lastMovedAfter]"] = last_moved_after
        if status:
            params["filter[status]"] = status
        async with httpx.AsyncClient(timeout=30, headers=self.headers) as client:
            resp = await client.get(f"{self.base_url}/api/v1/businesses", params=params)
            resp.raise_for_status()
            return self._extract_data(resp.json())

    async def get_business(self, business_id: str) -> dict:
        async with httpx.AsyncClient(timeout=30, headers=self.headers) as client:
            resp = await client.get(f"{self.base_url}/api/v1/businesses/{business_id}")
            resp.raise_for_status()
            return resp.json()

    async def get_lead(self, lead_id: str) -> dict:
        async with httpx.AsyncClient(timeout=30, headers=self.headers) as client:
            resp = await client.get(f"{self.base_url}/api/v1/leads/{lead_id}")
            resp.raise_for_status()
            return resp.json()

    async def list_leads(self, limit: int = 50) -> list:
        async with httpx.AsyncClient(timeout=30, headers=self.headers) as client:
            resp = await client.get(f"{self.base_url}/api/v1/leads", params={"limit": limit})
            resp.raise_for_status()
            return self._extract_data(resp.json())

    async def health_check(self) -> dict:
        """Testa conexão com DataCrazy API."""
        if not self.configured:
            return {"status": "not_configured", "message": "DATACRAZY_API_TOKEN not set"}
        try:
            pipelines = await self.list_pipelines()
            return {"status": "ok", "pipelines_count": len(pipelines) if isinstance(pipelines, list) else 0}
        except httpx.HTTPStatusError as e:
            return {"status": "error", "message": f"HTTP {e.response.status_code}"}
        except httpx.RequestError as e:
            return {"status": "error", "message": str(e)}
