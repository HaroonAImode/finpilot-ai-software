"""Revenue Manager's chart/KPI data — architecture report §5.3 plan,
docs/superpowers/specs/2026-08-27-revenue-manager-scan-design.md §6.4.

Kept separate from schemas/invoice.py: this is aggregated reporting shape,
not a view of one Invoice row.
"""
from pydantic import BaseModel


class MonthlyRevenuePoint(BaseModel):
    #: "YYYY-MM" — a plain calendar month, not a datetime, since these are
    #: chart buckets rather than a specific point in time.
    month: str
    total: float
    count: int


class TopCustomerPoint(BaseModel):
    customer_name: str
    total: float
    count: int


class SalesSummaryTotals(BaseModel):
    revenue: float
    document_count: int
    #: needs_review + needs_review_high_priority — the number of scanned
    #: sales still awaiting a human's confirmation before they should be
    #: trusted as real revenue.
    pending_review: int
    #: Sales whose invoice_date (or scan date, as a fallback — see
    #: sales_summary()) falls on the server's current date. Added for
    #: Transactions Service's Dashboard "Today's Revenue" KPI, which has no
    #: other way to get day-level granularity out of this otherwise
    #: monthly-bucketed summary.
    today_revenue: float = 0.0


class SalesSummaryResponse(BaseModel):
    monthly: list[MonthlyRevenuePoint]
    top_customers: list[TopCustomerPoint]
    totals: SalesSummaryTotals
