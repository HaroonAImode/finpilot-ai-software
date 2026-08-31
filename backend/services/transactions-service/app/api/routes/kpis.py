"""Dashboard KPIs and trend charts — architecture report §5.4's dashboard
endpoints. Revenue-side figures are read from Invoice Service's own
scanned-sales summary (see services/invoice_service_client.py) rather than
duplicated into a table here — see the design spec's scope note.
"""
import logging
from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.tenancy import get_company_id
from app.db.session import get_db
from app.models import Expense, ExpenseStatus
from app.schemas.kpis import CashFlowPoint, KpiSet, MonthlyPoint
from app.services.invoice_service_client import (
    InvoiceServiceError, fetch_invoice_status_counts, fetch_sales_summary,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/transactions", tags=["transactions"])

#: Matches the frontend's existing 7-month dummy trend (data.ts's
#: revenueVsExpenses/cashFlow arrays) — same window, now backed by real data.
_TREND_MONTHS = 7


def _month_bounds(today: date) -> tuple[date, date]:
    start = today.replace(day=1)
    end = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
    return start, end


def _last_n_month_keys(today: date, n: int) -> list[str]:
    """The last n calendar months as "YYYY-MM" keys, oldest first, ending
    at the current month."""
    keys = []
    year, month = today.year, today.month
    for _ in range(n):
        keys.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return list(reversed(keys))


def _short_month_label(key: str) -> str:
    year, month = key.split("-")
    return date(int(year), int(month), 1).strftime("%b")


async def _expenses_by_month(db: AsyncSession, company_id: UUID) -> dict[str, float]:
    """Approved expenses grouped by calendar month, in Python rather than a
    Postgres-only date_trunc — this service's own test suite runs on
    SQLite, the same reasoning invoice-service's unlinked_vendor_groups
    already used for the same tradeoff. Row counts here are bounded by one
    company's expense history, not a volume this would struggle with."""
    rows = (
        await db.execute(
            select(Expense.date, Expense.amount_pkr)
            .where(Expense.company_id == company_id, Expense.status == ExpenseStatus.approved)
        )
    ).all()
    totals: dict[str, float] = {}
    for expense_date, amount in rows:
        key = expense_date.strftime("%Y-%m")
        totals[key] = totals.get(key, 0.0) + (amount or 0.0)
    return totals


async def _all_time_approved_expenses(db: AsyncSession, company_id: UUID) -> float:
    return (
        await db.scalar(
            select(func.coalesce(func.sum(Expense.amount_pkr), 0.0))
            .where(Expense.company_id == company_id, Expense.status == ExpenseStatus.approved)
        )
    ) or 0.0


async def _salaries_this_month(db: AsyncSession, company_id: UUID) -> float:
    start, end = _month_bounds(date.today())
    return (
        await db.scalar(
            select(func.coalesce(func.sum(Expense.amount_pkr), 0.0))
            .where(
                Expense.company_id == company_id, Expense.status == ExpenseStatus.approved,
                Expense.category == "Salaries", Expense.date >= start, Expense.date < end,
            )
        )
    ) or 0.0


@router.get("/kpis", response_model=KpiSet)
async def kpis(
    company_id: UUID = Depends(get_company_id), db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> KpiSet:
    start, end = _month_bounds(date.today())
    monthly_expenses = (
        await db.scalar(
            select(func.coalesce(func.sum(Expense.amount_pkr), 0.0))
            .where(
                Expense.company_id == company_id, Expense.status == ExpenseStatus.approved,
                Expense.date >= start, Expense.date < end,
            )
        )
    ) or 0.0
    employee_salaries = await _salaries_this_month(db, company_id)
    all_time_expenses = await _all_time_approved_expenses(db, company_id)

    try:
        summary = await fetch_sales_summary(settings, company_id)
        status_counts = await fetch_invoice_status_counts(settings, company_id)
        today_revenue = summary["totals"]["today_revenue"]
        all_time_revenue = summary["totals"]["revenue"]
        this_month_key = date.today().strftime("%Y-%m")
        monthly_revenue = next(
            (p["total"] for p in summary["monthly"] if p["month"] == this_month_key), 0.0,
        )
        counts = status_counts["counts"]
        pending_invoices = counts.get("needs_review", 0) + counts.get("needs_review_high_priority", 0)
        processed_invoices = (
            counts.get("processed", 0) + counts.get("validated", 0) + counts.get("sent_to_accounting", 0)
        )
        unavailable = False
    except InvoiceServiceError as exc:
        logger.warning("Revenue-side KPIs unavailable: %s", exc)
        today_revenue = monthly_revenue = all_time_revenue = 0.0
        pending_invoices = processed_invoices = 0
        unavailable = True

    return KpiSet(
        today_revenue=round(today_revenue, 2),
        monthly_revenue=round(monthly_revenue, 2),
        monthly_expenses=round(monthly_expenses, 2),
        net_profit=round(monthly_revenue - monthly_expenses, 2),
        cash_balance=round(all_time_revenue - all_time_expenses, 2),
        employee_salaries=round(employee_salaries, 2),
        pending_invoices=pending_invoices,
        processed_invoices=processed_invoices,
        revenue_unavailable=unavailable,
    )


@router.get("/chart/revenue-vs-expenses", response_model=list[MonthlyPoint])
async def revenue_vs_expenses(
    company_id: UUID = Depends(get_company_id), db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> list[MonthlyPoint]:
    months = _last_n_month_keys(date.today(), _TREND_MONTHS)
    expenses_by_month = await _expenses_by_month(db, company_id)

    revenue_by_month: dict[str, float] = {}
    try:
        summary = await fetch_sales_summary(settings, company_id)
        revenue_by_month = {p["month"]: p["total"] for p in summary["monthly"]}
    except InvoiceServiceError as exc:
        logger.warning("Revenue side of the revenue-vs-expenses chart unavailable: %s", exc)

    return [
        MonthlyPoint(
            month=_short_month_label(key),
            revenue=round(revenue_by_month.get(key, 0.0), 2),
            expenses=round(expenses_by_month.get(key, 0.0), 2),
        )
        for key in months
    ]


@router.get("/chart/cash-flow", response_model=list[CashFlowPoint])
async def cash_flow(
    company_id: UUID = Depends(get_company_id), db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> list[CashFlowPoint]:
    """Same underlying monthly data as revenue-vs-expenses, reshaped — the
    frontend uses a distinct chart component for inflow/outflow, not a
    distinct dataset."""
    points = await revenue_vs_expenses(company_id=company_id, db=db, settings=settings)
    return [CashFlowPoint(month=p.month, inflow=p.revenue, outflow=p.expenses) for p in points]
