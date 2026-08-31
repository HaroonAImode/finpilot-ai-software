# Imported under an alias: every model below has a field literally named
# `date`, and Pydantic v2 resolves a field's own annotation using a
# namespace that already contains that field's default value under the
# same name — `date: Optional[date] = None` silently resolves the
# annotation to NoneType instead of datetime.date (confirmed with a
# minimal repro; only bites fields that have a default, which is exactly
# ExpenseUpdate's and BookFromInvoicePayload's own case below). Aliasing
# the import sidesteps the collision entirely rather than relying on every
# field staying required forever.
from datetime import date as _date, datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

PAYMENT_METHODS = Literal["bank_transfer", "online", "cheque", "cash", "card"]


class ExpenseCreate(BaseModel):
    category: str = Field(min_length=1, max_length=100)
    vendor_name: Optional[str] = Field(default=None, max_length=500)
    date: _date
    amount_pkr: float = Field(gt=0)
    payment_method: Optional[PAYMENT_METHODS] = None
    reference_id: Optional[str] = Field(default=None, max_length=200)

    @field_validator("category")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("category cannot be blank")
        return cleaned


class ExpenseUpdate(BaseModel):
    """Every field optional; only those actually present are applied — the
    same model_fields_set convention used across this codebase's other PUT
    endpoints (e.g. vendors-service's VendorUpdate)."""

    category: Optional[str] = Field(default=None, min_length=1, max_length=100)
    vendor_name: Optional[str] = Field(default=None, max_length=500)
    date: Optional[_date] = None
    amount_pkr: Optional[float] = Field(default=None, gt=0)
    payment_method: Optional[PAYMENT_METHODS] = None
    reference_id: Optional[str] = Field(default=None, max_length=200)


class ExpenseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    category: str
    vendor_name: Optional[str] = None
    vendor_id: Optional[UUID] = None
    invoice_id: Optional[UUID] = None
    date: _date
    amount_pkr: float
    payment_method: Optional[str] = None
    status: str
    source: str
    reference_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ExpenseListResponse(BaseModel):
    expenses: list[ExpenseResponse]
    total: int
    skip: int
    limit: int


class ExpenseOptions(BaseModel):
    """Suggested values the UI can offer. Deliberately not enforced — see
    the model's SUGGESTED_CATEGORIES for why."""

    categories: list[str]
    payment_methods: list[str]


class CategoryBreakdownPoint(BaseModel):
    name: str
    value: float


class ExpenseSummary(BaseModel):
    """The Expenses page's 4 summary cards — Total/Approved/Pending
    Approval/Rejected, for one period (a calendar month by default)."""

    total: float
    approved: float
    pending: float
    rejected: float
    pending_count: int
    rejected_count: int


class BookFromInvoicePayload(BaseModel):
    """Invoice Service's own hand-off shape — see
    invoice-service/app/services/transactions_service_client.py.
    `payment_method` arrives as that service's own bank/cash value, mapped
    onto this service's richer PaymentMethod set in the route handler, not
    validated against it here — a value this service doesn't recognise
    should book the expense with no payment method rather than reject the
    whole hand-off.
    """

    invoice_id: UUID
    vendor_id: Optional[UUID] = None
    vendor_name: Optional[str] = None
    reference_id: Optional[str] = None
    date: Optional[_date] = None
    amount_pkr: float
    payment_method: Optional[str] = None


class BookFromPayrollPayload(BaseModel):
    """HR Service's own hand-off shape after processing a payroll run —
    see hr-service/app/services/transactions_service_client.py.
    `reference_id` is generated per processing batch there (not just per
    period), so two legitimate calls in the same calendar month each book
    their own Expense rather than the second colliding with the first."""

    period: str
    amount_pkr: float
    reference_id: str
    vendor_name: Optional[str] = None
