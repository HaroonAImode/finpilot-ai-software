import enum
import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum, Float, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

#: Architecture report §5.4's own list. Suggestions, not an enforced enum —
#: same reasoning as vendors-service's SUGGESTED_CATEGORIES: a real SME's
#: expense categories do not fit seven buckets, and a new one would
#: otherwise need an ALTER TYPE migration before someone could type it.
SUGGESTED_CATEGORIES = (
    "Salaries", "Raw Material", "Fuel & Transport", "Utilities", "Rent", "Marketing", "Office",
)


class PaymentMethod(str, enum.Enum):
    """Architecture report §5.4's Expense.payment_method set — richer than
    Invoice Service's own bank/cash pair, because an expense can be paid in
    more ways than an invoice can be marked settled. book-from-invoice maps
    that narrower set onto this one on the way in (see routes/expenses.py)."""

    bank_transfer = "bank_transfer"
    online = "online"
    cheque = "cheque"
    cash = "cash"
    card = "card"


class ExpenseStatus(str, enum.Enum):
    #: Typed in directly on the Expenses page — needs a human's approval
    #: before it counts as booked spend.
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class ExpenseSource(str, enum.Enum):
    manual = "manual"
    #: Auto-created when a purchase invoice was sent to accounting in
    #: Invoice Service (see invoice-service's transactions_service_client.py).
    #: Starts approved — it already went through that service's own
    #: review/validate lifecycle before reaching here.
    invoice = "invoice"
    #: Auto-created when HR Service processes a payroll run (see
    #: hr-service's transactions_service_client.py). Starts approved —
    #: employees were actually paid by the time this call is made.
    payroll = "payroll"


class Expense(Base):
    """One outflow — either typed in directly, or booked automatically when
    a purchase invoice was sent to accounting (source=invoice, invoice_id
    set). The two are one table, not two: an approved expense is an
    approved expense regardless of how it was entered, the same reasoning
    Invoice Service's own `extraction_source` column applies to purchase vs
    generated invoices sharing one table.

    Revenue is deliberately **not** a sibling table here — see the
    Transactions Service design spec's scope note. It stays Invoice
    Service's own scanned-sales ledger (Revenue Manager), read from over
    HTTP for the Dashboard's revenue-side KPIs and charts.
    """

    __tablename__ = "expense"
    __table_args__ = (
        # An invoice can be booked at most once — send_to_accounting's own
        # status guard means Invoice Service can only fire the hook once in
        # practice, but this is the defense-in-depth version of that
        # guarantee, and it is what makes book-from-invoice safely retryable.
        UniqueConstraint("invoice_id", name="uq_expense_invoice_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    category: Mapped[str] = mapped_column(String(100), nullable=False)
    vendor_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: Not a foreign key — Vendors Service owns this id in its own database
    #: (Rule 1: no service reads another's database directly).
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    #: Likewise not a foreign key — Invoice Service owns the invoice.
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, unique=True)

    date: Mapped[date] = mapped_column(Date, nullable=False)
    amount_pkr: Mapped[float] = mapped_column(Float, nullable=False)
    payment_method: Mapped[PaymentMethod | None] = mapped_column(
        Enum(PaymentMethod, name="expense_payment_method"), nullable=True,
    )
    status: Mapped[ExpenseStatus] = mapped_column(
        Enum(ExpenseStatus, name="expense_status"), nullable=False, default=ExpenseStatus.pending,
    )
    source: Mapped[ExpenseSource] = mapped_column(
        Enum(ExpenseSource, name="expense_source"), nullable=False, default=ExpenseSource.manual,
    )
    #: Free-text external reference — a purchase invoice's own
    #: invoice_number when source=invoice, or whatever an accountant types
    #: for a manual entry.
    reference_id: Mapped[str | None] = mapped_column(String(200), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now(),
    )
