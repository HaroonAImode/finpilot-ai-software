"""Payroll processing — idempotency per employee, the summary's "what it
would cost today" math, history aggregation, and the Transactions Service
booking hook's degrade-not-fail behaviour (patched throughout — this is
about *this* service's own logic, not about Transactions Service actually
being reachable).
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
from app.models import Employee, PayrollRecord
from app.models.base import Base
from app.services.transactions_service_client import TransactionsServiceError

COMPANY_ID = uuid.uuid4()
THIS_PERIOD = date.today().strftime("%Y-%m")


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
def booking_up():
    with patch(
        "app.api.routes.payroll.book_payroll_expense", AsyncMock(return_value=None),
    ) as mock:
        yield mock


@pytest.fixture
def booking_down():
    with patch(
        "app.api.routes.payroll.book_payroll_expense",
        AsyncMock(side_effect=TransactionsServiceError("down")),
    ):
        yield


async def _seed_employee(db_session_factory, **overrides) -> uuid.UUID:
    employee = Employee(
        id=uuid.uuid4(), company_id=overrides.pop("company_id", COMPANY_ID),
        name=overrides.pop("name", "Employee"), department=overrides.pop("department", "Finance"),
        salary_pkr=overrides.pop("salary_pkr", 100_000.0), bonus_pkr=overrides.pop("bonus_pkr", 0.0),
        deductions_pkr=overrides.pop("deductions_pkr", 0.0),
        joining_date=overrides.pop("joining_date", date(2024, 1, 1)),
        active=overrides.pop("active", True),
    )
    async with db_session_factory() as session:
        session.add(employee)
        await session.commit()
    return employee.id


def _seed_employee_sync(db_session_factory, **kwargs) -> uuid.UUID:
    import asyncio

    return asyncio.get_event_loop().run_until_complete(_seed_employee(db_session_factory, **kwargs))


class TestPayrollSummary:
    def test_totals_across_active_employees(self, client, db_session_factory) -> None:
        _seed_employee_sync(db_session_factory, salary_pkr=100_000, bonus_pkr=10_000, deductions_pkr=5_000)
        _seed_employee_sync(db_session_factory, salary_pkr=50_000, bonus_pkr=0, deductions_pkr=2_000)
        body = client.get("/api/v1/employees/payroll").json()
        assert body["employee_count"] == 2
        assert body["total_salary"] == 150_000.0
        assert body["total_net"] == 100_000 + 10_000 - 5_000 + 50_000 - 2_000
        assert body["pending_count"] == 2

    def test_inactive_employees_are_excluded(self, client, db_session_factory) -> None:
        _seed_employee_sync(db_session_factory, active=False)
        assert client.get("/api/v1/employees/payroll").json()["employee_count"] == 0

    def test_not_parsed_as_an_employee_id(self, client) -> None:
        assert client.get("/api/v1/employees/payroll").status_code == 200


class TestProcessPayroll:
    def test_pays_every_active_employee(self, client, db_session_factory, booking_up) -> None:
        _seed_employee_sync(db_session_factory, salary_pkr=100_000)
        _seed_employee_sync(db_session_factory, salary_pkr=50_000)

        response = client.post("/api/v1/employees/payroll/process")
        body = response.json()

        assert body["processed_count"] == 2
        assert body["already_processed_count"] == 0
        assert body["total_net"] == 150_000.0
        assert body["expense_booked"] is True

    def test_a_second_call_in_the_same_month_pays_no_one_twice(self, client, db_session_factory, booking_up) -> None:
        _seed_employee_sync(db_session_factory, salary_pkr=100_000)
        client.post("/api/v1/employees/payroll/process")

        second = client.post("/api/v1/employees/payroll/process").json()

        assert second["processed_count"] == 0
        assert second["already_processed_count"] == 1
        assert second["total_net"] == 0.0
        # Nothing new to book — booking is skipped entirely, not re-sent.
        assert second["expense_booked"] is False

    def test_a_newly_added_employee_is_covered_by_the_next_call(self, client, db_session_factory, booking_up) -> None:
        _seed_employee_sync(db_session_factory, salary_pkr=100_000)
        client.post("/api/v1/employees/payroll/process")

        _seed_employee_sync(db_session_factory, salary_pkr=50_000)
        second = client.post("/api/v1/employees/payroll/process").json()

        assert second["processed_count"] == 1
        assert second["already_processed_count"] == 1
        assert second["total_net"] == 50_000.0
        assert second["expense_booked"] is True

    def test_inactive_employees_are_not_paid(self, client, db_session_factory, booking_up) -> None:
        _seed_employee_sync(db_session_factory, active=False)
        body = client.post("/api/v1/employees/payroll/process").json()
        assert body["processed_count"] == 0
        assert body["expense_booked"] is False

    def test_transactions_service_being_unreachable_does_not_fail_the_run(
        self, client, db_session_factory, booking_down,
    ) -> None:
        _seed_employee_sync(db_session_factory, salary_pkr=100_000)
        response = client.post("/api/v1/employees/payroll/process")
        body = response.json()
        assert response.status_code == 200
        assert body["processed_count"] == 1
        assert body["expense_booked"] is False

    def test_a_paid_employee_gets_a_payroll_record(self, client, db_session_factory, booking_up) -> None:
        employee_id = _seed_employee_sync(db_session_factory, salary_pkr=100_000)
        client.post("/api/v1/employees/payroll/process")
        body = client.get("/api/v1/employees/").json()
        assert body["employees"][0]["payment_status"] == "paid"


class TestPayrollHistory:
    async def _seed_record(self, db_session_factory, **overrides) -> None:
        record = PayrollRecord(
            id=uuid.uuid4(), company_id=overrides.pop("company_id", COMPANY_ID),
            employee_id=overrides.pop("employee_id", uuid.uuid4()),
            period=overrides.pop("period", "2026-07"),
            salary_pkr=overrides.pop("salary_pkr", 100_000.0), bonus_pkr=0.0, deductions_pkr=0.0,
            net_salary_pkr=overrides.pop("net_salary_pkr", 100_000.0),
        )
        async with db_session_factory() as session:
            session.add(record)
            await session.commit()

    def _seed_record_sync(self, db_session_factory, **kwargs) -> None:
        import asyncio

        asyncio.get_event_loop().run_until_complete(self._seed_record(db_session_factory, **kwargs))

    def test_grouped_by_period_newest_first(self, client, db_session_factory) -> None:
        self._seed_record_sync(db_session_factory, period="2026-06", net_salary_pkr=100_000)
        self._seed_record_sync(db_session_factory, period="2026-07", net_salary_pkr=200_000)
        body = client.get("/api/v1/employees/payroll/history").json()
        assert [e["period"] for e in body["entries"]] == ["2026-07", "2026-06"]

    def test_totals_sum_across_employees_in_the_same_period(self, client, db_session_factory) -> None:
        self._seed_record_sync(db_session_factory, period="2026-07", net_salary_pkr=100_000)
        self._seed_record_sync(db_session_factory, period="2026-07", net_salary_pkr=50_000)
        body = client.get("/api/v1/employees/payroll/history").json()
        assert body["entries"][0]["total_net"] == 150_000.0
        assert body["entries"][0]["employee_count"] == 2

    def test_empty_history_is_valid(self, client) -> None:
        assert client.get("/api/v1/employees/payroll/history").json() == {"entries": [], "total": 0}
