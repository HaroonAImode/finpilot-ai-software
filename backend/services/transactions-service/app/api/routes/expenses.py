"""Expense ledger — architecture report §5.4's Expense half of Transactions
Service. Revenue stays out of this service entirely; see kpis.py and the
design spec's scope note for why.
"""
import logging
from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import get_company_id
from app.db.session import get_db
from app.models import SUGGESTED_CATEGORIES, Expense, ExpenseSource, ExpenseStatus, PaymentMethod
from app.schemas.expense import (
    BookFromInvoicePayload, BookFromPayrollPayload, CategoryBreakdownPoint, ExpenseCreate,
    ExpenseListResponse, ExpenseOptions, ExpenseResponse, ExpenseSummary, ExpenseUpdate,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/expenses", tags=["expenses"])

#: Invoice Service's own PaymentMethod is a narrower bank/cash pair, mapped
#: onto this service's richer set on the way in from book-from-invoice. An
#: unrecognised or absent value books with no payment method rather than
#: rejecting the whole hand-off.
_INVOICE_PAYMENT_METHOD_MAP = {"bank": "bank_transfer", "cash": "cash"}

#: A scanned/OCR'd purchase invoice carries no expense category — the rules
#: engine (docs/invoice-ocr-plan.md) never attempted to infer one, and
#: guessing wrong would be worse than picking the single most common bucket
#: for a Pakistani SME's supplier spend and leaving it correctable via PUT.
_DEFAULT_INVOICE_EXPENSE_CATEGORY = "Raw Material"


def _month_bounds(today: date) -> tuple[date, date]:
    start = today.replace(day=1)
    end = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
    return start, end


async def _get_expense_scoped(db: AsyncSession, expense_id: UUID, company_id: UUID) -> Expense:
    expense = await db.scalar(
        select(Expense).where(Expense.id == expense_id, Expense.company_id == company_id)
    )
    if expense is None:
        raise HTTPException(status_code=404, detail="Expense not found")
    return expense


@router.get("/options", response_model=ExpenseOptions)
async def expense_options() -> ExpenseOptions:
    """Suggested categories and payment methods for the UI. Registered
    before /{expense_id} so the literal path is not parsed as an id."""
    return ExpenseOptions(
        categories=list(SUGGESTED_CATEGORIES), payment_methods=[m.value for m in PaymentMethod],
    )


@router.get("/categories", response_model=list[CategoryBreakdownPoint])
async def category_breakdown(
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
) -> list[CategoryBreakdownPoint]:
    """Category split for the Expenses/Dashboard donut chart — this
    calendar month by default, approved expenses only: a pending or
    rejected entry is not real spend yet.
    """
    start, end = (date_from, date_to) if date_from and date_to else _month_bounds(date.today())
    rows = (
        await db.execute(
            select(Expense.category, func.coalesce(func.sum(Expense.amount_pkr), 0.0))
            .where(
                Expense.company_id == company_id, Expense.status == ExpenseStatus.approved,
                Expense.date >= start, Expense.date < end,
            )
            .group_by(Expense.category)
        )
    ).all()
    return sorted(
        (CategoryBreakdownPoint(name=category, value=round(total, 2)) for category, total in rows),
        key=lambda p: p.value, reverse=True,
    )


@router.get("/summary", response_model=ExpenseSummary)
async def expense_summary(
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
) -> ExpenseSummary:
    """The Expenses page's 4 summary cards, this calendar month by default."""
    start, end = (date_from, date_to) if date_from and date_to else _month_bounds(date.today())
    rows = (
        await db.execute(
            select(Expense.status, Expense.amount_pkr)
            .where(Expense.company_id == company_id, Expense.date >= start, Expense.date < end)
        )
    ).all()

    total = approved = pending = rejected = 0.0
    pending_count = rejected_count = 0
    for status, amount in rows:
        amount = amount or 0.0
        total += amount
        if status == ExpenseStatus.approved:
            approved += amount
        elif status == ExpenseStatus.pending:
            pending += amount
            pending_count += 1
        elif status == ExpenseStatus.rejected:
            rejected += amount
            rejected_count += 1

    return ExpenseSummary(
        total=round(total, 2), approved=round(approved, 2), pending=round(pending, 2),
        rejected=round(rejected, 2), pending_count=pending_count, rejected_count=rejected_count,
    )


@router.post("/book-from-invoice", response_model=ExpenseResponse)
async def book_from_invoice(
    payload: BookFromInvoicePayload,
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
) -> ExpenseResponse:
    """Invoice Service's send-to-accounting hand-off for a purchase invoice.

    Idempotent on invoice_id (the unique constraint below) rather than
    erroring on a repeat call: send-to-accounting's own status guard means
    Invoice Service can only fire this once in practice, but a client-side
    retry after a dropped response should return the already-booked record,
    not a 409.
    """
    existing = await db.scalar(
        select(Expense).where(Expense.company_id == company_id, Expense.invoice_id == payload.invoice_id)
    )
    if existing is not None:
        return ExpenseResponse.model_validate(existing)

    mapped_method = _INVOICE_PAYMENT_METHOD_MAP.get(payload.payment_method or "")
    expense = Expense(
        company_id=company_id,
        category=_DEFAULT_INVOICE_EXPENSE_CATEGORY,
        vendor_name=payload.vendor_name,
        vendor_id=payload.vendor_id,
        invoice_id=payload.invoice_id,
        date=payload.date or date.today(),
        amount_pkr=payload.amount_pkr,
        payment_method=PaymentMethod(mapped_method) if mapped_method else None,
        status=ExpenseStatus.approved,
        source=ExpenseSource.invoice,
        reference_id=payload.reference_id,
    )
    db.add(expense)
    await db.commit()
    await db.refresh(expense)
    return ExpenseResponse.model_validate(expense)


@router.post("/book-from-payroll", response_model=ExpenseResponse)
async def book_from_payroll(
    payload: BookFromPayrollPayload,
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
) -> ExpenseResponse:
    """HR Service's payroll-processing hand-off.

    Idempotent on (company_id, source=payroll, reference_id) as a courtesy
    against an exact request retry — not a hard guarantee (no DB
    constraint backs it, unlike book-from-invoice's invoice_id unique
    index), because reference_id is not a natural key here the way
    invoice_id is. The real idempotency for the common case lives on HR
    Service's side: it only calls this endpoint when it just recorded a
    genuinely new amount to book, so a second legitimate call within the
    same period always carries a different reference_id and a different
    amount.
    """
    existing = await db.scalar(
        select(Expense).where(
            Expense.company_id == company_id, Expense.source == ExpenseSource.payroll,
            Expense.reference_id == payload.reference_id,
        )
    )
    if existing is not None:
        return ExpenseResponse.model_validate(existing)

    expense = Expense(
        company_id=company_id,
        category="Salaries",
        vendor_name=payload.vendor_name or f"Payroll — {payload.period}",
        date=date.today(),
        amount_pkr=payload.amount_pkr,
        status=ExpenseStatus.approved,
        source=ExpenseSource.payroll,
        reference_id=payload.reference_id,
    )
    db.add(expense)
    await db.commit()
    await db.refresh(expense)
    return ExpenseResponse.model_validate(expense)


@router.get("/", response_model=ExpenseListResponse)
async def list_expenses(
    category: Optional[str] = Query(None),
    status: Optional[str] = Query(None, description="pending, approved or rejected"),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    search: Optional[str] = Query(None, description="Case-insensitive match on vendor_name"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
) -> ExpenseListResponse:
    query = select(Expense).where(Expense.company_id == company_id)
    count_query = select(func.count()).select_from(Expense).where(Expense.company_id == company_id)

    if category:
        query = query.where(Expense.category == category)
        count_query = count_query.where(Expense.category == category)
    if status:
        try:
            resolved = ExpenseStatus(status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Unknown expense status '{status}'")
        query = query.where(Expense.status == resolved)
        count_query = count_query.where(Expense.status == resolved)
    if date_from:
        query = query.where(Expense.date >= date_from)
        count_query = count_query.where(Expense.date >= date_from)
    if date_to:
        query = query.where(Expense.date <= date_to)
        count_query = count_query.where(Expense.date <= date_to)
    if search:
        pattern = f"%{search}%"
        query = query.where(Expense.vendor_name.ilike(pattern))
        count_query = count_query.where(Expense.vendor_name.ilike(pattern))

    total = await db.scalar(count_query) or 0
    rows = (
        await db.execute(
            query.order_by(Expense.date.desc(), Expense.created_at.desc()).offset(skip).limit(limit)
        )
    ).scalars().all()

    return ExpenseListResponse(
        expenses=[ExpenseResponse.model_validate(row) for row in rows], total=total, skip=skip, limit=limit,
    )


@router.post("/", response_model=ExpenseResponse, status_code=201)
async def create_expense(
    payload: ExpenseCreate,
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
) -> ExpenseResponse:
    """A manually typed expense — starts pending, per architecture report
    §5.4's own approve/reject workflow below."""
    expense = Expense(
        company_id=company_id, category=payload.category, vendor_name=payload.vendor_name,
        date=payload.date, amount_pkr=payload.amount_pkr,
        payment_method=PaymentMethod(payload.payment_method) if payload.payment_method else None,
        status=ExpenseStatus.pending, source=ExpenseSource.manual, reference_id=payload.reference_id,
    )
    db.add(expense)
    await db.commit()
    await db.refresh(expense)
    return ExpenseResponse.model_validate(expense)


@router.get("/{expense_id}", response_model=ExpenseResponse)
async def get_expense(
    expense_id: UUID, company_id: UUID = Depends(get_company_id), db: AsyncSession = Depends(get_db),
) -> ExpenseResponse:
    expense = await _get_expense_scoped(db, expense_id, company_id)
    return ExpenseResponse.model_validate(expense)


@router.put("/{expense_id}", response_model=ExpenseResponse)
async def update_expense(
    expense_id: UUID, payload: ExpenseUpdate,
    company_id: UUID = Depends(get_company_id), db: AsyncSession = Depends(get_db),
) -> ExpenseResponse:
    """A correction after the fact — including on an auto-booked,
    source=invoice expense, since `_DEFAULT_INVOICE_EXPENSE_CATEGORY` is a
    guess an accountant should be able to fix."""
    expense = await _get_expense_scoped(db, expense_id, company_id)
    for field_name, value in payload.model_dump(exclude_unset=True).items():
        if field_name == "payment_method" and value is not None:
            value = PaymentMethod(value)
        setattr(expense, field_name, value)
    await db.commit()
    await db.refresh(expense)
    return ExpenseResponse.model_validate(expense)


@router.put("/{expense_id}/approve", response_model=ExpenseResponse)
async def approve_expense(
    expense_id: UUID, company_id: UUID = Depends(get_company_id), db: AsyncSession = Depends(get_db),
) -> ExpenseResponse:
    expense = await _get_expense_scoped(db, expense_id, company_id)
    expense.status = ExpenseStatus.approved
    await db.commit()
    await db.refresh(expense)
    return ExpenseResponse.model_validate(expense)


@router.put("/{expense_id}/reject", response_model=ExpenseResponse)
async def reject_expense(
    expense_id: UUID, company_id: UUID = Depends(get_company_id), db: AsyncSession = Depends(get_db),
) -> ExpenseResponse:
    expense = await _get_expense_scoped(db, expense_id, company_id)
    expense.status = ExpenseStatus.rejected
    await db.commit()
    await db.refresh(expense)
    return ExpenseResponse.model_validate(expense)
