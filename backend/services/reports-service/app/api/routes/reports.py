"""Report generation — architecture report §5.9. Generation is
synchronous: every report here is a handful of HTTP calls plus in-memory
aggregation, nothing OCR-slow, so there is no queue to build for it (see
the design spec §2). A downstream failure aborts the request with a 502
and creates no Report row — the same "the response IS the data" reasoning
`invoices.py::vendor_invoices` already applies, not a persisted `failed`
state to retry later.
"""
import logging
from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.tenancy import get_company_id
from app.db.session import get_db
from app.models import Report, ReportType
from app.schemas.payload import ReportPayload
from app.schemas.report import ReportListItem, ReportListResponse, ReportResponse
from app.schemas.requests import BalanceSheetRequest, ReportPeriodRequest
from app.services.excel_renderer import render_excel
from app.services.invoice_service_client import InvoiceServiceError
from app.services.pdf_renderer import render_pdf
from app.services.report_generator import (
    generate_balance_sheet, generate_cash_flow, generate_profit_loss, generate_purchase_report,
    generate_sales_report, generate_tax_summary,
)
from app.services.settings_service_client import SettingsServiceError, fetch_company_profile
from app.services.transactions_service_client import TransactionsServiceError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/reports", tags=["reports"])

_UPSTREAM_ERRORS = (InvoiceServiceError, TransactionsServiceError, SettingsServiceError)


def _to_response(report: Report) -> ReportResponse:
    return ReportResponse(
        id=report.id, type=report.type.value, period_start=report.period_start, period_end=report.period_end,
        status=report.status.value, payload=ReportPayload(**report.payload_json),
        generated_at=report.generated_at, created_at=report.created_at,
    )


async def _attach_company_name(settings: Settings, company_id: UUID, payload: ReportPayload) -> None:
    """Best-effort only — a report is still a complete, correct report
    without its issuing company's name on it, unlike the financial figures
    every generate_* function fetches, which are fatal to miss."""
    try:
        profile = await fetch_company_profile(settings, company_id)
        payload.company_name = profile.get("name")
    except SettingsServiceError as exc:
        logger.warning("Could not attach company name to report: %s", exc)


async def _generate_and_save(
    db: AsyncSession, settings: Settings, company_id: UUID, report_type: ReportType,
    period_start: date, period_end: date, payload: ReportPayload,
) -> Report:
    await _attach_company_name(settings, company_id, payload)
    report = Report(
        company_id=company_id, type=report_type, period_start=period_start, period_end=period_end,
        payload_json=payload.model_dump(mode="json"),
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)
    return report


@router.post("/profit-loss", response_model=ReportResponse)
async def create_profit_loss(
    payload: ReportPeriodRequest, company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings),
) -> ReportResponse:
    try:
        result = await generate_profit_loss(settings, company_id, payload.period_start, payload.period_end)
    except _UPSTREAM_ERRORS as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    report = await _generate_and_save(
        db, settings, company_id, ReportType.profit_loss, payload.period_start, payload.period_end, result,
    )
    return _to_response(report)


@router.post("/cash-flow", response_model=ReportResponse)
async def create_cash_flow(
    payload: ReportPeriodRequest, company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings),
) -> ReportResponse:
    try:
        result = await generate_cash_flow(settings, company_id, payload.period_start, payload.period_end)
    except _UPSTREAM_ERRORS as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    report = await _generate_and_save(
        db, settings, company_id, ReportType.cash_flow, payload.period_start, payload.period_end, result,
    )
    return _to_response(report)


@router.post("/tax-summary", response_model=ReportResponse)
async def create_tax_summary(
    payload: ReportPeriodRequest, company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings),
) -> ReportResponse:
    try:
        result = await generate_tax_summary(settings, company_id, payload.period_start, payload.period_end)
    except _UPSTREAM_ERRORS as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    report = await _generate_and_save(
        db, settings, company_id, ReportType.tax_summary, payload.period_start, payload.period_end, result,
    )
    return _to_response(report)


@router.post("/sales", response_model=ReportResponse)
async def create_sales_report(
    payload: ReportPeriodRequest, company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings),
) -> ReportResponse:
    try:
        result = await generate_sales_report(settings, company_id, payload.period_start, payload.period_end)
    except _UPSTREAM_ERRORS as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    report = await _generate_and_save(
        db, settings, company_id, ReportType.sales, payload.period_start, payload.period_end, result,
    )
    return _to_response(report)


@router.post("/purchases", response_model=ReportResponse)
async def create_purchase_report(
    payload: ReportPeriodRequest, company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings),
) -> ReportResponse:
    try:
        result = await generate_purchase_report(settings, company_id, payload.period_start, payload.period_end)
    except _UPSTREAM_ERRORS as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    report = await _generate_and_save(
        db, settings, company_id, ReportType.purchases, payload.period_start, payload.period_end, result,
    )
    return _to_response(report)


@router.post("/balance-sheet", response_model=ReportResponse)
async def create_balance_sheet(
    payload: BalanceSheetRequest, company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings),
) -> ReportResponse:
    try:
        result = await generate_balance_sheet(settings, company_id, payload.as_of_date)
    except _UPSTREAM_ERRORS as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    report = await _generate_and_save(
        db, settings, company_id, ReportType.balance_sheet, payload.as_of_date, payload.as_of_date, result,
    )
    return _to_response(report)


@router.get("/", response_model=ReportListResponse)
async def list_reports(
    type: Optional[str] = Query(None, description="Filter by report type"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
) -> ReportListResponse:
    query = select(Report).where(Report.company_id == company_id)
    count_query = select(func.count()).select_from(Report).where(Report.company_id == company_id)
    if type:
        try:
            resolved = ReportType(type)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Unknown report type '{type}'")
        query = query.where(Report.type == resolved)
        count_query = count_query.where(Report.type == resolved)

    total = await db.scalar(count_query) or 0
    rows = (
        await db.execute(query.order_by(Report.generated_at.desc()).offset(skip).limit(limit))
    ).scalars().all()

    return ReportListResponse(
        reports=[ReportListItem.model_validate(r) for r in rows], total=total, skip=skip, limit=limit,
    )


async def _get_report_scoped(db: AsyncSession, report_id: UUID, company_id: UUID) -> Report:
    report = await db.scalar(select(Report).where(Report.id == report_id, Report.company_id == company_id))
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


@router.get("/{report_id}", response_model=ReportResponse)
async def get_report(
    report_id: UUID, company_id: UUID = Depends(get_company_id), db: AsyncSession = Depends(get_db),
) -> ReportResponse:
    return _to_response(await _get_report_scoped(db, report_id, company_id))


@router.get("/{report_id}/pdf")
async def get_report_pdf(
    report_id: UUID, company_id: UUID = Depends(get_company_id), db: AsyncSession = Depends(get_db),
) -> Response:
    report = await _get_report_scoped(db, report_id, company_id)
    pdf_bytes = render_pdf(ReportPayload(**report.payload_json))
    return Response(
        content=pdf_bytes, media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{report.type.value}-{report.id}.pdf"'},
    )


@router.get("/{report_id}/excel")
async def get_report_excel(
    report_id: UUID, company_id: UUID = Depends(get_company_id), db: AsyncSession = Depends(get_db),
) -> Response:
    report = await _get_report_scoped(db, report_id, company_id)
    excel_bytes = render_excel(ReportPayload(**report.payload_json))
    return Response(
        content=excel_bytes, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{report.type.value}-{report.id}.xlsx"'},
    )
