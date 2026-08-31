import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PayrollRecord(Base):
    """One employee's actual pay for one calendar month — an immutable
    snapshot taken at processing time, not a live view of Employee's
    current figures. Unlike Expense's `vendor_id`/`invoice_id` (which name
    records in a *different* service's database), `employee_id` here is a
    real foreign key: both tables live in this service's own database.

    `(employee_id, period)` is unique, which is what makes `POST
    /employees/payroll/process` safely idempotent per employee — running it
    twice in the same month pays no one twice.
    """

    __tablename__ = "payroll_record"
    __table_args__ = (
        UniqueConstraint("employee_id", "period", name="uq_payroll_record_employee_period"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employee.id", ondelete="CASCADE"), nullable=False, index=True,
    )

    #: "YYYY-MM" — a calendar month, not a specific date, matching Invoice
    #: Service's own monthly chart bucketing convention.
    period: Mapped[str] = mapped_column(String(7), nullable=False)

    #: Snapshotted from Employee at processing time, not FK'd through —
    #: this is what was actually paid, regardless of what Employee says today.
    salary_pkr: Mapped[float] = mapped_column(Float, nullable=False)
    bonus_pkr: Mapped[float] = mapped_column(Float, nullable=False)
    deductions_pkr: Mapped[float] = mapped_column(Float, nullable=False)
    net_salary_pkr: Mapped[float] = mapped_column(Float, nullable=False)

    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
