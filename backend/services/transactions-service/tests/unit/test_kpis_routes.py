"""Dashboard KPIs and trend charts — the expense-side math (this service's
own data) and the revenue-side degrade-rather-than-fail behaviour (Invoice
Service is patched throughout, same as vendors-service's own suite patches
its Invoice Service client — this is about *this* service's aggregation,
not about another one being reachable).
"""
import uuid
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.tenancy import get_company_id
from app.db.session import get_db
from app.main import app
from app.models import Expense, ExpenseStatus
from app.models.base import Base
from app.services.invoice_service_client import InvoiceServiceError

COMPANY_ID = uuid.uuid4()
TODAY = date.today()
THIS_MONTH_KEY = TODAY.strftime("%Y-%m")

SALES_SUMMARY = {
    "monthly": [{"month": THIS_MONTH_KEY, "total": 500_000.0, "count": 3}],
    "top_customers": [],
    "totals": {"revenue": 1_500_000.0, "document_count": 5, "pending_review": 1, "today_revenue": 20_000.0},
}
STATUS_COUNTS = {
    "counts": {
        "processed": 10, "needs_review": 2, "needs_review_high_priority": 1,
        "validated": 3, "sent_to_accounting": 4,
    },
    "total": 20,
}


@pytest.fixture
def db_session_factory():
    import asyncio

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.get_event_loop().run_until_complete(_setup())
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def client(db_session_factory):
    async def _db():
        async with db_session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_company_id] = lambda: COMPANY_ID
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def invoice_service_up():
    with (
        patch("app.api.routes.kpis.fetch_sales_summary", AsyncMock(return_value=SALES_SUMMARY)),
        patch("app.api.routes.kpis.fetch_invoice_status_counts", AsyncMock(return_value=STATUS_COUNTS)),
    ):
        yield


@pytest.fixture
def invoice_service_down():
    with patch(
        "app.api.routes.kpis.fetch_sales_summary", AsyncMock(side_effect=InvoiceServiceError("down")),
    ):
        yield


async def _seed(db_session_factory, **overrides) -> None:
    expense = Expense(
        id=uuid.uuid4(), company_id=COMPANY_ID,
        category=overrides.pop("category", "Raw Material"),
        date=overrides.pop("date", TODAY),
        amount_pkr=overrides.pop("amount_pkr", 10_000.0),
        status=overrides.pop("status", ExpenseStatus.approved),
        **overrides,
    )
    async with db_session_factory() as session:
        session.add(expense)
        await session.commit()


def _seed_sync(db_session_factory, **kwargs) -> None:
    import asyncio

    asyncio.get_event_loop().run_until_complete(_seed(db_session_factory, **kwargs))


class TestKpis:
    def test_combines_expenses_and_revenue(self, client, db_session_factory, invoice_service_up) -> None:
        _seed_sync(db_session_factory, category="Rent", amount_pkr=200_000)
        _seed_sync(db_session_factory, category="Salaries", amount_pkr=150_000)
        body = client.get("/api/v1/transactions/kpis").json()

        assert body["monthly_expenses"] == 350_000.0
        assert body["monthly_revenue"] == 500_000.0
        assert body["today_revenue"] == 20_000.0
        assert body["net_profit"] == 150_000.0
        assert body["employee_salaries"] == 150_000.0
        assert body["cash_balance"] == 1_500_000.0 - 350_000.0
        assert body["pending_invoices"] == 3
        assert body["processed_invoices"] == 17
        assert body["revenue_unavailable"] is False

    def test_pending_expenses_do_not_count_as_monthly_expenses(self, client, db_session_factory, invoice_service_up) -> None:
        _seed_sync(db_session_factory, amount_pkr=999_999, status=ExpenseStatus.pending)
        body = client.get("/api/v1/transactions/kpis").json()
        assert body["monthly_expenses"] == 0.0

    def test_degrades_when_invoice_service_is_unreachable(self, client, db_session_factory, invoice_service_down) -> None:
        _seed_sync(db_session_factory, amount_pkr=50_000)
        body = client.get("/api/v1/transactions/kpis").json()
        assert body["revenue_unavailable"] is True
        assert body["monthly_revenue"] == 0.0
        # Expense-side figures are this service's own data — still correct
        # even when the downstream call fails.
        assert body["monthly_expenses"] == 50_000.0


class TestRevenueVsExpensesChart:
    def test_returns_seven_months_ending_at_the_current_one(self, client, invoice_service_up) -> None:
        body = client.get("/api/v1/transactions/chart/revenue-vs-expenses").json()
        assert len(body) == 7
        assert body[-1]["month"] == date.today().strftime("%b")

    def test_the_current_month_reflects_both_sides(self, client, db_session_factory, invoice_service_up) -> None:
        _seed_sync(db_session_factory, amount_pkr=75_000)
        body = client.get("/api/v1/transactions/chart/revenue-vs-expenses").json()
        assert body[-1]["revenue"] == 500_000.0
        assert body[-1]["expenses"] == 75_000.0

    def test_degrades_the_revenue_side_only(self, client, db_session_factory, invoice_service_down) -> None:
        _seed_sync(db_session_factory, amount_pkr=42_000)
        body = client.get("/api/v1/transactions/chart/revenue-vs-expenses").json()
        assert body[-1]["revenue"] == 0.0
        assert body[-1]["expenses"] == 42_000.0


class TestCashFlowChart:
    def test_reshapes_the_same_data_as_inflow_outflow(self, client, db_session_factory, invoice_service_up) -> None:
        _seed_sync(db_session_factory, amount_pkr=60_000)
        body = client.get("/api/v1/transactions/chart/cash-flow").json()
        assert body[-1]["inflow"] == 500_000.0
        assert body[-1]["outflow"] == 60_000.0
