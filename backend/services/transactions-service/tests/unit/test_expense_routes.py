"""Expense ledger — CRUD, tenancy scoping, the approve/reject lifecycle,
and the book-from-invoice hand-off's idempotency (the property that makes
it safe for Invoice Service to retry).
"""
import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.tenancy import get_company_id
from app.db.session import get_db
from app.main import app
from app.models import Expense, ExpenseSource, ExpenseStatus
from app.models.base import Base

COMPANY_ID = uuid.uuid4()
OTHER_COMPANY_ID = uuid.uuid4()
TODAY = date.today()

VALID = {
    "category": "Raw Material", "vendor_name": "ABC Traders",
    "date": TODAY.isoformat(), "amount_pkr": 68_400, "payment_method": "bank_transfer",
    "reference_id": "REF-001",
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


async def _seed(db_session_factory, **overrides) -> uuid.UUID:
    expense = Expense(
        id=uuid.uuid4(),
        company_id=overrides.pop("company_id", COMPANY_ID),
        category=overrides.pop("category", "Raw Material"),
        date=overrides.pop("date", TODAY),
        amount_pkr=overrides.pop("amount_pkr", 10_000.0),
        status=overrides.pop("status", ExpenseStatus.pending),
        source=overrides.pop("source", ExpenseSource.manual),
        **overrides,
    )
    async with db_session_factory() as session:
        session.add(expense)
        await session.commit()
    return expense.id


def _seed_sync(db_session_factory, **kwargs) -> uuid.UUID:
    import asyncio

    return asyncio.get_event_loop().run_until_complete(_seed(db_session_factory, **kwargs))


class TestCreate:
    def test_an_expense_is_created_pending(self, client) -> None:
        response = client.post("/api/v1/expenses/", json=VALID)
        assert response.status_code == 201
        body = response.json()
        assert body["category"] == "Raw Material"
        assert body["status"] == "pending"
        assert body["source"] == "manual"

    def test_a_blank_category_is_rejected(self, client) -> None:
        assert client.post("/api/v1/expenses/", json={**VALID, "category": "   "}).status_code == 422

    def test_a_zero_amount_is_rejected(self, client) -> None:
        assert client.post("/api/v1/expenses/", json={**VALID, "amount_pkr": 0}).status_code == 422

    def test_an_unknown_payment_method_is_rejected(self, client) -> None:
        assert client.post("/api/v1/expenses/", json={**VALID, "payment_method": "crypto"}).status_code == 422

    def test_payment_method_may_be_omitted(self, client) -> None:
        payload = {k: v for k, v in VALID.items() if k != "payment_method"}
        assert client.post("/api/v1/expenses/", json=payload).json()["payment_method"] is None


class TestListAndFilter:
    def test_only_this_company_s_expenses_are_listed(self, client, db_session_factory) -> None:
        _seed_sync(db_session_factory, category="Rent")
        _seed_sync(db_session_factory, category="Rent", company_id=OTHER_COMPANY_ID)
        body = client.get("/api/v1/expenses/").json()
        assert body["total"] == 1

    def test_filter_by_category(self, client, db_session_factory) -> None:
        _seed_sync(db_session_factory, category="Rent")
        _seed_sync(db_session_factory, category="Utilities")
        body = client.get("/api/v1/expenses/?category=Rent").json()
        assert body["total"] == 1
        assert body["expenses"][0]["category"] == "Rent"

    def test_filter_by_status(self, client, db_session_factory) -> None:
        _seed_sync(db_session_factory, status=ExpenseStatus.approved)
        _seed_sync(db_session_factory, status=ExpenseStatus.pending)
        body = client.get("/api/v1/expenses/?status=approved").json()
        assert body["total"] == 1

    def test_an_unknown_status_filter_is_a_clear_400(self, client) -> None:
        assert client.get("/api/v1/expenses/?status=bogus").status_code == 400

    def test_filter_by_date_range(self, client, db_session_factory) -> None:
        _seed_sync(db_session_factory, date=TODAY - timedelta(days=60))
        _seed_sync(db_session_factory, date=TODAY)
        body = client.get(
            f"/api/v1/expenses/?date_from={(TODAY - timedelta(days=1)).isoformat()}"
        ).json()
        assert body["total"] == 1

    def test_search_matches_vendor_name_case_insensitively(self, client, db_session_factory) -> None:
        _seed_sync(db_session_factory, vendor_name="Sindh Fuels Ltd.")
        _seed_sync(db_session_factory, vendor_name="K-Electric")
        body = client.get("/api/v1/expenses/?search=sindh").json()
        assert body["total"] == 1


class TestLifecycle:
    def test_approve_transitions_status(self, client, db_session_factory) -> None:
        expense_id = _seed_sync(db_session_factory)
        response = client.put(f"/api/v1/expenses/{expense_id}/approve")
        assert response.json()["status"] == "approved"

    def test_reject_transitions_status(self, client, db_session_factory) -> None:
        expense_id = _seed_sync(db_session_factory)
        response = client.put(f"/api/v1/expenses/{expense_id}/reject")
        assert response.json()["status"] == "rejected"

    def test_approving_another_company_s_expense_is_a_404(self, client, db_session_factory) -> None:
        expense_id = _seed_sync(db_session_factory, company_id=OTHER_COMPANY_ID)
        assert client.put(f"/api/v1/expenses/{expense_id}/approve").status_code == 404

    def test_a_correction_updates_only_the_fields_sent(self, client, db_session_factory) -> None:
        expense_id = _seed_sync(db_session_factory, category="Raw Material", amount_pkr=1000.0)
        response = client.put(f"/api/v1/expenses/{expense_id}", json={"category": "Utilities"})
        body = response.json()
        assert body["category"] == "Utilities"
        assert body["amount_pkr"] == 1000.0

    def test_getting_a_missing_expense_is_a_404(self, client) -> None:
        assert client.get(f"/api/v1/expenses/{uuid.uuid4()}").status_code == 404


class TestCategoryBreakdown:
    def test_only_approved_expenses_count(self, client, db_session_factory) -> None:
        _seed_sync(db_session_factory, category="Rent", amount_pkr=500_000, status=ExpenseStatus.approved)
        _seed_sync(db_session_factory, category="Rent", amount_pkr=999_999, status=ExpenseStatus.pending)
        body = client.get("/api/v1/expenses/categories").json()
        assert body == [{"name": "Rent", "value": 500_000.0}]

    def test_sorted_descending_by_amount(self, client, db_session_factory) -> None:
        _seed_sync(db_session_factory, category="Office", amount_pkr=100, status=ExpenseStatus.approved)
        _seed_sync(db_session_factory, category="Rent", amount_pkr=500_000, status=ExpenseStatus.approved)
        body = client.get("/api/v1/expenses/categories").json()
        assert [p["name"] for p in body] == ["Rent", "Office"]

    def test_expenses_outside_the_month_are_excluded(self, client, db_session_factory) -> None:
        _seed_sync(
            db_session_factory, category="Rent", amount_pkr=500_000,
            status=ExpenseStatus.approved, date=TODAY - timedelta(days=400),
        )
        assert client.get("/api/v1/expenses/categories").json() == []


class TestSummary:
    def test_the_four_buckets_are_computed_correctly(self, client, db_session_factory) -> None:
        _seed_sync(db_session_factory, amount_pkr=100, status=ExpenseStatus.approved)
        _seed_sync(db_session_factory, amount_pkr=200, status=ExpenseStatus.pending)
        _seed_sync(db_session_factory, amount_pkr=300, status=ExpenseStatus.rejected)
        body = client.get("/api/v1/expenses/summary").json()
        assert body == {
            "total": 600.0, "approved": 100.0, "pending": 200.0, "rejected": 300.0,
            "pending_count": 1, "rejected_count": 1,
        }


class TestOptions:
    def test_suggested_values_are_returned(self, client) -> None:
        body = client.get("/api/v1/expenses/options").json()
        assert "Raw Material" in body["categories"]
        assert "bank_transfer" in body["payment_methods"]


class TestBookFromInvoice:
    PAYLOAD = {
        "invoice_id": str(uuid.uuid4()), "vendor_id": str(uuid.uuid4()),
        "vendor_name": "Karachi Steel Co.", "reference_id": "INV-2026-1840",
        "date": TODAY.isoformat(), "amount_pkr": 96_200.0, "payment_method": "bank",
    }

    def test_books_an_approved_expense_with_the_default_category(self, client) -> None:
        response = client.post("/api/v1/expenses/book-from-invoice", json=self.PAYLOAD)
        body = response.json()
        assert body["status"] == "approved"
        assert body["source"] == "invoice"
        assert body["category"] == "Raw Material"
        assert body["payment_method"] == "bank_transfer"
        assert body["invoice_id"] == self.PAYLOAD["invoice_id"]

    def test_a_repeat_call_for_the_same_invoice_returns_the_existing_record(self, client) -> None:
        first = client.post("/api/v1/expenses/book-from-invoice", json=self.PAYLOAD).json()
        second = client.post("/api/v1/expenses/book-from-invoice", json=self.PAYLOAD).json()
        assert first["id"] == second["id"]
        assert client.get("/api/v1/expenses/").json()["total"] == 1

    def test_cash_maps_straight_through(self, client) -> None:
        payload = {**self.PAYLOAD, "invoice_id": str(uuid.uuid4()), "payment_method": "cash"}
        assert client.post("/api/v1/expenses/book-from-invoice", json=payload).json()["payment_method"] == "cash"

    def test_a_missing_payment_method_books_with_none(self, client) -> None:
        payload = {**self.PAYLOAD, "invoice_id": str(uuid.uuid4()), "payment_method": None}
        assert client.post("/api/v1/expenses/book-from-invoice", json=payload).json()["payment_method"] is None

    def test_a_missing_date_falls_back_to_today(self, client) -> None:
        payload = {**self.PAYLOAD, "invoice_id": str(uuid.uuid4()), "date": None}
        assert client.post("/api/v1/expenses/book-from-invoice", json=payload).json()["date"] == TODAY.isoformat()


class TestBookFromPayroll:
    PAYLOAD = {"period": "2026-08", "amount_pkr": 350_000.0, "reference_id": "PAYROLL-2026-08-abc123"}

    def test_books_an_approved_salaries_expense(self, client) -> None:
        body = client.post("/api/v1/expenses/book-from-payroll", json=self.PAYLOAD).json()
        assert body["category"] == "Salaries"
        assert body["status"] == "approved"
        assert body["source"] == "payroll"
        assert body["vendor_name"] == "Payroll — 2026-08"
        assert body["amount_pkr"] == 350_000.0

    def test_a_repeat_call_with_the_same_reference_returns_the_existing_record(self, client) -> None:
        first = client.post("/api/v1/expenses/book-from-payroll", json=self.PAYLOAD).json()
        second = client.post("/api/v1/expenses/book-from-payroll", json=self.PAYLOAD).json()
        assert first["id"] == second["id"]
        assert client.get("/api/v1/expenses/").json()["total"] == 1

    def test_two_different_batches_in_the_same_period_both_book(self, client) -> None:
        """Distinct reference_ids (as HR Service generates per batch) must
        not collide — each is a real, separate payroll expense."""
        client.post("/api/v1/expenses/book-from-payroll", json=self.PAYLOAD)
        second_payload = {**self.PAYLOAD, "reference_id": "PAYROLL-2026-08-def456", "amount_pkr": 50_000.0}
        client.post("/api/v1/expenses/book-from-payroll", json=second_payload)
        assert client.get("/api/v1/expenses/").json()["total"] == 2

    def test_this_shows_up_in_the_salaries_category_breakdown(self, client) -> None:
        client.post("/api/v1/expenses/book-from-payroll", json=self.PAYLOAD)
        body = client.get("/api/v1/expenses/categories").json()
        assert body == [{"name": "Salaries", "value": 350_000.0}]
