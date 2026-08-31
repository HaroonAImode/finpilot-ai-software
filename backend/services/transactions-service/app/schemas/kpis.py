from pydantic import BaseModel


class MonthlyPoint(BaseModel):
    #: Short month label ("Jan", "Feb"...) — chart display shape, matching
    #: the frontend's existing revenueVsExpenses/cashFlow dummy data shape.
    month: str
    revenue: float
    expenses: float


class CashFlowPoint(BaseModel):
    month: str
    inflow: float
    outflow: float


class KpiSet(BaseModel):
    today_revenue: float
    monthly_revenue: float
    monthly_expenses: float
    net_profit: float
    #: Cumulative all-time revenue minus all-time approved expenses. Not a
    #: real bank balance — there is no cash/bank account model in this
    #: codebase yet — a derived approximation, documented as such in the
    #: design spec rather than presented as more precise than it is.
    cash_balance: float
    #: This month's approved Expense entries in the "Salaries" category —
    #: HR Service (architecture report §5.5) does not exist yet, so this is
    #: the real substitute rather than a fabricated payroll figure.
    employee_salaries: float
    pending_invoices: int
    processed_invoices: int
    #: True when Invoice Service could not be reached for the invoice-
    #: derived figures above (revenue, pending/processed counts) — same
    #: "unavailable, not a lying zero" convention vendors-service already
    #: established for spend.
    revenue_unavailable: bool = False
