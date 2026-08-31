from datetime import datetime

from pydantic import BaseModel


class PayrollSummary(BaseModel):
    """The current calendar month's payroll picture — `/app/employees`'
    KPI row. Figures come from Employee's *current* values for anyone not
    yet processed this month, so this is "what payroll will cost if run
    today," not a promise about what a completed run actually paid."""

    period: str
    total_salary: float
    total_bonus: float
    total_deductions: float
    total_net: float
    employee_count: int
    paid_count: int
    pending_count: int


class PayrollProcessResult(BaseModel):
    period: str
    #: Employees actually paid by *this* call.
    processed_count: int
    #: Employees who already had a PayrollRecord for this period before
    #: this call — processing is idempotent per employee, so calling this
    #: twice in one month does not pay anyone twice.
    already_processed_count: int
    #: Sum of net_salary_pkr for the employees processed_count covers.
    #: 0.0 when processed_count is 0 — nothing new to book.
    total_net: float
    #: Whether a matching Expense was booked in Transactions Service.
    #: False when there was nothing to book (processed_count == 0) or when
    #: the booking call itself failed — see the design spec for why a
    #: failure here does not roll back the payroll run itself.
    expense_booked: bool


class PayrollHistoryEntry(BaseModel):
    period: str
    total_net: float
    employee_count: int
    processed_at: datetime


class PayrollHistoryResponse(BaseModel):
    entries: list[PayrollHistoryEntry]
    total: int
