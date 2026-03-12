"""Endpoints de relatórios CRM inteligentes."""
import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_api_key, get_current_client
from app.core.database import get_db
from app.models.client import Client
from app.models.report import Report
from app.api.schemas import ReportGenerate, ReportResponse
from app.services.report_service import fetch_datacrazy_data, generate_analysis

router = APIRouter(prefix="/api/reports", tags=["Reports"])


@router.post("/generate", response_model=ReportResponse, status_code=201)
async def generate_report(
    body: ReportGenerate,
    db: AsyncSession = Depends(get_db),
    api_key: str = Depends(get_api_key),
):
    """Gera relatório inteligente de CRM.

    Se raw_data for passado, usa esses dados.
    Se não, tenta puxar do DataCrazy via API.
    """
    # Buscar client
    result = await db.execute(select(Client).where(Client.id == body.client_id))
    client = result.scalar_one_or_none()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    period_end = date.today()
    period_start = period_end - timedelta(days=body.period_days)

    # Criar report como pending
    report = Report(
        client_id=client.id,
        period_start=period_start,
        period_end=period_end,
        status="generating",
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)

    # Coletar dados
    if body.raw_data:
        raw_data = body.raw_data
        raw_data["source"] = "manual"
    else:
        raw_data = await fetch_datacrazy_data(client.crm_credentials, body.period_days)

    # Gerar análise
    analysis = await generate_analysis(raw_data, period_start, period_end)

    # Atualizar report
    report.raw_data = raw_data
    report.analysis = analysis
    report.status = "ready"
    await db.commit()
    await db.refresh(report)
    return report


@router.get("", response_model=list[ReportResponse])
async def list_reports(
    client_id: uuid.UUID | None = Query(None),
    limit: int = Query(20, le=100),
    db: AsyncSession = Depends(get_db),
    api_key: str = Depends(get_api_key),
    auth_client: Client | None = Depends(get_current_client),
):
    query = select(Report).order_by(Report.created_at.desc()).limit(limit)
    if auth_client:
        query = query.where(Report.client_id == auth_client.id)
    elif client_id:
        query = query.where(Report.client_id == client_id)

    result = await db.execute(query)
    return result.scalars().all()


@router.get("/latest", response_model=ReportResponse)
async def latest_report(
    client_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    api_key: str = Depends(get_api_key),
):
    result = await db.execute(
        select(Report)
        .where(Report.client_id == client_id, Report.status == "ready")
        .order_by(Report.created_at.desc())
        .limit(1)
    )
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="No reports found for this client")
    return report


@router.get("/{report_id}", response_model=ReportResponse)
async def get_report(
    report_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    api_key: str = Depends(get_api_key),
):
    result = await db.execute(select(Report).where(Report.id == report_id))
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return report
