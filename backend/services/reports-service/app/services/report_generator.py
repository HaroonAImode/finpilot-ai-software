"""Computes each report type's ReportPayload from other services' data.

Every compute function here raises straight through on a downstream
failure (`InvoiceServiceError`/`TransactionsServiceError`/
`SettingsServiceError`) — the route layer catches those and turns them
into a 502, per config.py's own reasoning: the downstream data these
calls fetch *is* the report, so a failure here is fatal, not degraded.
"""
from datetime import date
from uuid import UUID

from app.core.config import Settings
from app.schemas.payload import ReportPayload, ReportTable, SummaryLine
from app.services.invoice_service_client import fetch_sales_summary, fetch_vendor_spend
from app.services.settings_service_client import fetch_tax_settings
from app.services.transactions_service_client import (
    fetch_all_approved_expenses, fetch_expense_categories, fetch_expense_summary,
)

#: Balance Sheet needs "all approved expenses up to as_of_date" — an
#: open-ended lower bound Transactions Service's own endpoints don't
#: support directly (both bounds are required there, or it falls back to
#: "this calendar month" — see transactions_service_client.py). This
#: sentinel stands in for "the beginning of this company's history,"
#: which is a safe assumption for a platform that didn't exist before 2026.
_ALL_TIME_START = date(2000, 1, 1)


def _money(n: float) -> str:
    return f"PKR {n:,.0f}"


def _period_label(period_start: date, period_end: date) -> str:
    fmt = "%d %b %Y"
    if period_start == period_end:
        return f"As of {period_start.strftime(fmt)}"
    return f"{period_start.strftime(fmt)} – {period_end.strftime(fmt)}"


async def generate_profit_loss(
    settings: Settings, company_id: UUID, period_start: date, period_end: date,
) -> ReportPayload:
    sales = await fetch_sales_summary(settings, company_id, date_from=period_start, date_to=period_end, customer_limit=1)
    expense_summary = await fetch_expense_summary(settings, company_id, date_from=period_start, date_to=period_end)
    categories = await fetch_expense_categories(settings, company_id, date_from=period_start, date_to=period_end)

    revenue = sales["totals"]["revenue"]
    expenses = expense_summary["approved"]

    table = None
    if categories:
        ranked = sorted(categories, key=lambda c: c["value"], reverse=True)
        table = ReportTable(headers=["Category", "Amount"], rows=[[c["name"], _money(c["value"])] for c in ranked])

    return ReportPayload(
        title="Profit & Loss Statement",
        period_label=_period_label(period_start, period_end),
        summary=[
            SummaryLine(label="Revenue", value=_money(revenue)),
            SummaryLine(label="Expenses (approved)", value=_money(expenses)),
            SummaryLine(label="Net Profit", value=_money(revenue - expenses)),
        ],
        table=table,
        notes=[
            "Revenue is scanned or authored sales recorded in this period, regardless of review status — "
            "the same figure Revenue Manager's own \"Revenue Captured\" KPI shows.",
            "Expenses are approved entries only; pending or rejected expenses are not counted as spend.",
        ],
    )


async def generate_cash_flow(
    settings: Settings, company_id: UUID, period_start: date, period_end: date,
) -> ReportPayload:
    sales = await fetch_sales_summary(settings, company_id, date_from=period_start, date_to=period_end, customer_limit=1)
    expenses = await fetch_all_approved_expenses(settings, company_id, date_from=period_start, date_to=period_end)

    revenue_by_month: dict[str, float] = {p["month"]: p["total"] for p in sales["monthly"]}
    expense_by_month: dict[str, float] = {}
    for expense in expenses:
        month = expense["date"][:7]  # "YYYY-MM-DD" -> "YYYY-MM"
        expense_by_month[month] = expense_by_month.get(month, 0.0) + expense["amount_pkr"]

    months = sorted(set(revenue_by_month) | set(expense_by_month))
    total_inflow = sum(revenue_by_month.values())
    total_outflow = sum(expense_by_month.values())

    table = None
    if months:
        table = ReportTable(
            headers=["Month", "Inflow", "Outflow", "Net"],
            rows=[
                [
                    month, _money(revenue_by_month.get(month, 0.0)), _money(expense_by_month.get(month, 0.0)),
                    _money(revenue_by_month.get(month, 0.0) - expense_by_month.get(month, 0.0)),
                ]
                for month in months
            ],
        )

    return ReportPayload(
        title="Cash Flow Statement",
        period_label=_period_label(period_start, period_end),
        summary=[
            SummaryLine(label="Total Inflow", value=_money(total_inflow)),
            SummaryLine(label="Total Outflow", value=_money(total_outflow)),
            SummaryLine(label="Net Cash Flow", value=_money(total_inflow - total_outflow)),
        ],
        table=table,
        notes=["Inflow is scanned/authored sales revenue; outflow is approved expenses only — both by their own resolved date."],
    )


async def generate_tax_summary(
    settings: Settings, company_id: UUID, period_start: date, period_end: date,
) -> ReportPayload:
    sales = await fetch_sales_summary(settings, company_id, date_from=period_start, date_to=period_end, customer_limit=1)
    expense_summary = await fetch_expense_summary(settings, company_id, date_from=period_start, date_to=period_end)
    tax = await fetch_tax_settings(settings, company_id)

    revenue = sales["totals"]["revenue"]
    expenses = expense_summary["approved"]
    gst_rate = tax["default_gst_rate"]
    withholding_rate = tax["withholding_tax_rate"]

    output_gst = revenue * (gst_rate / 100)
    withholding = expenses * (withholding_rate / 100)

    return ReportPayload(
        title="Tax Summary",
        period_label=_period_label(period_start, period_end),
        summary=[
            SummaryLine(label="Filer Status", value="Filer" if tax["filer_status"] == "filer" else "Non-Filer"),
            SummaryLine(label="Gross Revenue", value=_money(revenue)),
            SummaryLine(label=f"Output GST ({gst_rate:g}%)", value=_money(output_gst)),
            SummaryLine(label=f"Withholding Tax ({withholding_rate:g}%)", value=_money(withholding)),
            SummaryLine(label="Estimated Net Tax Payable", value=_money(output_gst - withholding)),
        ],
        notes=[
            "A simplified estimate from this company's own configured rates (Settings → Tax Configuration) — "
            "not an FBR filing and not a substitute for professional tax advice.",
            "Input tax credit on purchases, exemptions, and category-specific withholding rules are not modelled.",
        ],
    )


async def generate_sales_report(
    settings: Settings, company_id: UUID, period_start: date, period_end: date,
) -> ReportPayload:
    sales = await fetch_sales_summary(
        settings, company_id, date_from=period_start, date_to=period_end, customer_limit=1000,
    )
    totals = sales["totals"]
    customers = sales["top_customers"]

    table = None
    if customers:
        table = ReportTable(
            headers=["Customer", "Documents", "Total"],
            rows=[[c["customer_name"], str(c["count"]), _money(c["total"])] for c in customers],
        )

    return ReportPayload(
        title="Sales Report",
        period_label=_period_label(period_start, period_end),
        summary=[
            SummaryLine(label="Total Revenue", value=_money(totals["revenue"])),
            SummaryLine(label="Documents Scanned", value=str(totals["document_count"])),
            SummaryLine(label="Pending Review", value=str(totals["pending_review"])),
        ],
        table=table,
        notes=[
            "Grouped by customer — this system has no sales-channel concept (Retail/Wholesale/Online/etc.) "
            "to report by instead, the same substitution the Dashboard's own charts already made.",
        ],
    )


async def generate_purchase_report(
    settings: Settings, company_id: UUID, period_start: date, period_end: date,
) -> ReportPayload:
    vendors = await fetch_vendor_spend(settings, company_id, date_from=period_start, date_to=period_end)
    ranked = sorted(vendors, key=lambda v: v["total_spend"], reverse=True)
    total_spend = sum(v["total_spend"] for v in vendors)

    table = None
    if ranked:
        table = ReportTable(
            headers=["Vendor", "Invoices", "Total Spend"],
            rows=[[v["vendor_name"] or "—", str(v["invoice_count"]), _money(v["total_spend"])] for v in ranked],
        )

    return ReportPayload(
        title="Purchase Report",
        period_label=_period_label(period_start, period_end),
        summary=[
            SummaryLine(label="Total Spend", value=_money(total_spend)),
            SummaryLine(label="Vendors", value=str(len(vendors))),
        ],
        table=table,
        notes=[
            "Only purchase invoices linked to a Vendors Service record are counted — an OCR'd vendor name not "
            "yet reconciled (see Vendor Reconciliation) has no vendor to attribute spend to.",
        ],
    )


async def generate_balance_sheet(settings: Settings, company_id: UUID, as_of_date: date) -> ReportPayload:
    sales = await fetch_sales_summary(
        settings, company_id, date_from=_ALL_TIME_START, date_to=as_of_date, customer_limit=1,
    )
    expense_summary = await fetch_expense_summary(settings, company_id, date_from=_ALL_TIME_START, date_to=as_of_date)
    cash_balance = sales["totals"]["revenue"] - expense_summary["approved"]

    return ReportPayload(
        title="Balance Sheet",
        period_label=_period_label(as_of_date, as_of_date),
        summary=[SummaryLine(label="Cash Balance (approx.)", value=_money(cash_balance))],
        notes=[
            "This is a simplified cash-position snapshot, not a full balance sheet: FinPilot does not yet track "
            "fixed assets, accounts receivable/payable, loans, or owner's equity.",
            "Cash Balance is cumulative all-time revenue minus cumulative approved expenses up to this date — the "
            "same derived approximation Transactions Service's own Dashboard KPI uses, not a real bank reconciliation.",
        ],
    )
