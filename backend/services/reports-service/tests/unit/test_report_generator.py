"""Each report type's own computation — the real business logic in this
service. All three upstream client modules are patched throughout: this
is about the arithmetic and shape of what gets computed, not about
whether Invoice/Transactions/Settings Service is reachable.
"""
import uuid
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from app.services import report_generator as rg

COMPANY_ID = uuid.uuid4()
START, END = date(2026, 7, 1), date(2026, 7, 31)

SALES_SUMMARY = {
    "monthly": [{"month": "2026-07", "total": 500_000.0, "count": 3}],
    "top_customers": [
        {"customer_name": "Al-Madina Retail", "total": 300_000.0, "count": 2},
        {"customer_name": "Shaheen Distributors", "total": 200_000.0, "count": 1},
    ],
    "totals": {"revenue": 500_000.0, "document_count": 3, "pending_review": 1, "today_revenue": 0.0},
}
EXPENSE_SUMMARY = {"total": 220_000.0, "approved": 200_000.0, "pending": 20_000.0, "rejected": 0.0, "pending_count": 1, "rejected_count": 0}
EXPENSE_CATEGORIES = [{"name": "Raw Material", "value": 150_000.0}, {"name": "Rent", "value": 50_000.0}]
VENDOR_SPEND = [
    {"vendor_id": str(uuid.uuid4()), "vendor_name": "ABC Traders", "total_spend": 120_000.0, "invoice_count": 3},
    {"vendor_id": str(uuid.uuid4()), "vendor_name": "Karachi Steel Co.", "total_spend": 80_000.0, "invoice_count": 1},
]
TAX_SETTINGS = {"default_gst_rate": 18.0, "withholding_tax_rate": 4.0, "filer_status": "filer"}
APPROVED_EXPENSES = [
    {"date": "2026-07-05", "amount_pkr": 100_000.0},
    {"date": "2026-07-20", "amount_pkr": 100_000.0},
]


@pytest.fixture(autouse=True)
def patched_clients():
    with (
        patch.object(rg, "fetch_sales_summary", AsyncMock(return_value=SALES_SUMMARY)),
        patch.object(rg, "fetch_expense_summary", AsyncMock(return_value=EXPENSE_SUMMARY)),
        patch.object(rg, "fetch_expense_categories", AsyncMock(return_value=EXPENSE_CATEGORIES)),
        patch.object(rg, "fetch_vendor_spend", AsyncMock(return_value=VENDOR_SPEND)),
        patch.object(rg, "fetch_tax_settings", AsyncMock(return_value=TAX_SETTINGS)),
        patch.object(rg, "fetch_all_approved_expenses", AsyncMock(return_value=APPROVED_EXPENSES)),
    ):
        yield


class TestProfitLoss:
    async def test_revenue_minus_expenses_equals_net_profit(self) -> None:
        payload = await rg.generate_profit_loss(None, COMPANY_ID, START, END)
        values = {s.label: s.value for s in payload.summary}
        assert values["Revenue"] == "PKR 500,000"
        assert values["Expenses (approved)"] == "PKR 200,000"
        assert values["Net Profit"] == "PKR 300,000"

    async def test_categories_become_a_sorted_table(self) -> None:
        payload = await rg.generate_profit_loss(None, COMPANY_ID, START, END)
        assert payload.table.headers == ["Category", "Amount"]
        assert payload.table.rows[0][0] == "Raw Material"


class TestCashFlow:
    async def test_inflow_outflow_and_net(self) -> None:
        payload = await rg.generate_cash_flow(None, COMPANY_ID, START, END)
        values = {s.label: s.value for s in payload.summary}
        assert values["Total Inflow"] == "PKR 500,000"
        assert values["Total Outflow"] == "PKR 200,000"
        assert values["Net Cash Flow"] == "PKR 300,000"

    async def test_monthly_breakdown_merges_both_sources(self) -> None:
        payload = await rg.generate_cash_flow(None, COMPANY_ID, START, END)
        assert payload.table.rows == [["2026-07", "PKR 500,000", "PKR 200,000", "PKR 300,000"]]


class TestTaxSummary:
    async def test_output_gst_and_withholding_are_computed_from_configured_rates(self) -> None:
        payload = await rg.generate_tax_summary(None, COMPANY_ID, START, END)
        values = {s.label: s.value for s in payload.summary}
        assert values["Filer Status"] == "Filer"
        assert values["Output GST (18%)"] == "PKR 90,000"
        assert values["Withholding Tax (4%)"] == "PKR 8,000"
        assert values["Estimated Net Tax Payable"] == "PKR 82,000"


class TestSalesReport:
    async def test_customers_table_and_totals(self) -> None:
        payload = await rg.generate_sales_report(None, COMPANY_ID, START, END)
        values = {s.label: s.value for s in payload.summary}
        assert values["Total Revenue"] == "PKR 500,000"
        assert payload.table.rows[0] == ["Al-Madina Retail", "2", "PKR 300,000"]

    async def test_channel_limitation_is_disclosed(self) -> None:
        payload = await rg.generate_sales_report(None, COMPANY_ID, START, END)
        assert any("channel" in note for note in payload.notes)


class TestPurchaseReport:
    async def test_vendors_ranked_by_spend(self) -> None:
        payload = await rg.generate_purchase_report(None, COMPANY_ID, START, END)
        values = {s.label: s.value for s in payload.summary}
        assert values["Total Spend"] == "PKR 200,000"
        assert payload.table.rows[0][0] == "ABC Traders"


class TestBalanceSheet:
    async def test_cash_balance_is_revenue_minus_expenses(self) -> None:
        payload = await rg.generate_balance_sheet(None, COMPANY_ID, date(2026, 7, 31))
        assert payload.summary[0].label == "Cash Balance (approx.)"
        assert payload.summary[0].value == "PKR 300,000"

    async def test_the_simplification_is_disclosed(self) -> None:
        payload = await rg.generate_balance_sheet(None, COMPANY_ID, date(2026, 7, 31))
        assert any("not a full balance sheet" in note for note in payload.notes)

    async def test_uses_an_open_lower_bound(self) -> None:
        """Balance Sheet needs all-time-up-to-date, not this-period —
        confirmed by checking the sentinel start date was actually passed."""
        await rg.generate_balance_sheet(None, COMPANY_ID, date(2026, 7, 31))
        call_kwargs = rg.fetch_sales_summary.call_args.kwargs
        assert call_kwargs["date_from"] == rg._ALL_TIME_START
