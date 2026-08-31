"""Payroll processing — architecture report §5.5's payroll endpoints.
Registered in main.py **before** employees_router: its literal paths
(`/employees/payroll`, `/employees/payroll/process`,
`/employees/payroll/history`) must be matched before employees_router's
`/{employee_id}` pattern gets a chance to treat "payroll" as an id.
"""
import logging
import uuid
from datetime import date, datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.tenancy import get_company_id
from app.db.session import get_db
from app.models import Employee, PayrollRecord
from app.schemas.payroll import (
    PayrollHistoryEntry, PayrollHistoryResponse, PayrollProcessResult, PayrollSummary,
)
from app.services.transactions_service_client import TransactionsServiceError, book_payroll_expense

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/employees/payroll", tags=["payroll"])


def _current_period() -> str:
    return date.today().strftime("%Y-%m")


@router.get("", response_model=PayrollSummary)
@router.get("/", response_model=PayrollSummary)
async def payroll_summary(
    company_id: UUID = Depends(get_company_id), db: AsyncSession = Depends(get_db),
) -> PayrollSummary:
    """This month's payroll picture — see the schema's own docstring for
    why this is "what it would cost to run today," not a completed run."""
    period = _current_period()
    employees = (
        await db.execute(select(Employee).where(Employee.company_id == company_id, Employee.active == True))  # noqa: E712
    ).scalars().all()

    paid_ids = set(
        (
            await db.execute(
                select(PayrollRecord.employee_id).where(
                    PayrollRecord.company_id == company_id, PayrollRecord.period == period,
                )
            )
        ).scalars().all()
    )

    total_salary = sum(e.salary_pkr for e in employees)
    total_bonus = sum(e.bonus_pkr for e in employees)
    total_deductions = sum(e.deductions_pkr for e in employees)

    return PayrollSummary(
        period=period,
        total_salary=round(total_salary, 2), total_bonus=round(total_bonus, 2),
        total_deductions=round(total_deductions, 2),
        total_net=round(total_salary + total_bonus - total_deductions, 2),
        employee_count=len(employees),
        paid_count=sum(1 for e in employees if e.id in paid_ids),
        pending_count=sum(1 for e in employees if e.id not in paid_ids),
    )


@router.post("/process", response_model=PayrollProcessResult)
async def process_payroll(
    company_id: UUID = Depends(get_company_id), db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PayrollProcessResult:
    """Pays every active employee not already paid this calendar month.

    Idempotent per employee via PayrollRecord's own unique constraint —
    calling this twice in one month creates records only for whoever
    wasn't covered by the first call (e.g. someone hired in between), and
    books a *second*, correctly-sized Expense for just that difference
    rather than either double-booking or silently dropping it. See the
    design spec for the reasoning.
    """
    period = _current_period()
    employees = (
        await db.execute(select(Employee).where(Employee.company_id == company_id, Employee.active == True))  # noqa: E712
    ).scalars().all()

    already_paid_ids = set(
        (
            await db.execute(
                select(PayrollRecord.employee_id).where(
                    PayrollRecord.company_id == company_id, PayrollRecord.period == period,
                )
            )
        ).scalars().all()
    )

    to_process = [e for e in employees if e.id not in already_paid_ids]
    now = datetime.now(timezone.utc)
    total_net = 0.0
    for employee in to_process:
        net = employee.salary_pkr + employee.bonus_pkr - employee.deductions_pkr
        total_net += net
        db.add(
            PayrollRecord(
                company_id=company_id, employee_id=employee.id, period=period,
                salary_pkr=employee.salary_pkr, bonus_pkr=employee.bonus_pkr,
                deductions_pkr=employee.deductions_pkr, net_salary_pkr=net, processed_at=now,
            )
        )
    await db.commit()

    expense_booked = False
    if to_process:
        # A batch-unique reference, not a fixed "PAYROLL-{period}" one —
        # two legitimate calls in the same month (e.g. a new hire added
        # mid-cycle) must each book their own Expense rather than the
        # second collide with and get swallowed by the first's.
        reference_id = f"PAYROLL-{period}-{uuid.uuid4().hex[:8]}"
        try:
            await book_payroll_expense(
                settings, company_id=company_id, period=period,
                amount=round(total_net, 2), reference_id=reference_id,
            )
            expense_booked = True
        except TransactionsServiceError as exc:
            # Logged loudly, not raised: the employees are still recorded
            # as paid in this service's own history above even if the
            # accounting side needs a manual follow-up — same reasoning as
            # Invoice Service's own send-to-accounting hook.
            logger.error("Failed to book payroll expense for period %s: %s", period, exc)

    return PayrollProcessResult(
        period=period, processed_count=len(to_process), already_processed_count=len(already_paid_ids),
        total_net=round(total_net, 2), expense_booked=expense_booked,
    )


@router.get("/history", response_model=PayrollHistoryResponse)
async def payroll_history(
    skip: int = Query(0, ge=0), limit: int = Query(12, ge=1, le=60),
    company_id: UUID = Depends(get_company_id), db: AsyncSession = Depends(get_db),
) -> PayrollHistoryResponse:
    """Past processed periods, most recent first — aggregated in Python
    rather than a Postgres-only date_trunc, this service's own test suite
    runs on SQLite (the same reasoning Invoice Service's
    unlinked_vendor_groups and Transactions Service's _expenses_by_month
    already use for the same tradeoff). Row counts here are bounded by
    "calendar months this company has run payroll for," never a volume
    this would struggle with.
    """
    rows = (
        await db.execute(
            select(PayrollRecord.period, PayrollRecord.net_salary_pkr, PayrollRecord.processed_at)
            .where(PayrollRecord.company_id == company_id)
        )
    ).all()

    by_period: dict[str, dict] = {}
    for period, net, processed_at in rows:
        bucket = by_period.setdefault(period, {"total_net": 0.0, "employee_count": 0, "processed_at": processed_at})
        bucket["total_net"] += net or 0.0
        bucket["employee_count"] += 1
        if processed_at and processed_at > bucket["processed_at"]:
            bucket["processed_at"] = processed_at

    entries = sorted(
        (
            PayrollHistoryEntry(
                period=period, total_net=round(b["total_net"], 2),
                employee_count=b["employee_count"], processed_at=b["processed_at"],
            )
            for period, b in by_period.items()
        ),
        key=lambda e: e.period, reverse=True,
    )
    return PayrollHistoryResponse(entries=entries[skip : skip + limit], total=len(entries))
