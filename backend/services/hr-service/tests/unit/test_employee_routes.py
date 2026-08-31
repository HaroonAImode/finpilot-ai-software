"""Employee directory — CRUD, tenancy scoping, and the computed
net_salary_pkr/payment_status fields (net_salary_pkr is always derived,
payment_status reflects this calendar month's PayrollRecord, if any)."""
import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.tenancy import get_company_id
from app.db.session import get_db
from app.main import app
from app.models import Employee, PayrollRecord
from app.models.base import Base

COMPANY_ID = uuid.uuid4()
OTHER_COMPANY_ID = uuid.uuid4()

VALID = {
    "name": "Ayesha Khan", "department": "Finance", "role": "Finance Manager", "salary_pkr": 185_000,
    "bonus_pkr": 20_000, "deductions_pkr": 12_000, "joining_date": "2024-01-15", "active": True,
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
    employee = Employee(
        id=uuid.uuid4(),
        company_id=overrides.pop("company_id", COMPANY_ID),
        name=overrides.pop("name", "Seeded Employee"),
        department=overrides.pop("department", "Finance"),
        salary_pkr=overrides.pop("salary_pkr", 100_000.0),
        bonus_pkr=overrides.pop("bonus_pkr", 0.0),
        deductions_pkr=overrides.pop("deductions_pkr", 0.0),
        joining_date=overrides.pop("joining_date", date(2024, 1, 1)),
        active=overrides.pop("active", True),
        **overrides,
    )
    async with db_session_factory() as session:
        session.add(employee)
        await session.commit()
    return employee.id


def _seed_sync(db_session_factory, **kwargs) -> uuid.UUID:
    import asyncio

    return asyncio.get_event_loop().run_until_complete(_seed(db_session_factory, **kwargs))


class TestCreate:
    def test_an_employee_is_created(self, client) -> None:
        response = client.post("/api/v1/employees/", json=VALID)
        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "Ayesha Khan"
        assert body["net_salary_pkr"] == 185_000 + 20_000 - 12_000
        assert body["payment_status"] == "pending"

    def test_a_blank_name_is_rejected(self, client) -> None:
        assert client.post("/api/v1/employees/", json={**VALID, "name": "   "}).status_code == 422

    def test_a_blank_role_is_rejected(self, client) -> None:
        assert client.post("/api/v1/employees/", json={**VALID, "role": "  "}).status_code == 422

    def test_a_missing_role_is_rejected(self, client) -> None:
        payload = {k: v for k, v in VALID.items() if k != "role"}
        assert client.post("/api/v1/employees/", json=payload).status_code == 422

    def test_a_zero_salary_is_rejected(self, client) -> None:
        assert client.post("/api/v1/employees/", json={**VALID, "salary_pkr": 0}).status_code == 422

    def test_bonus_and_deductions_default_to_zero(self, client) -> None:
        payload = {k: v for k, v in VALID.items() if k not in ("bonus_pkr", "deductions_pkr")}
        body = client.post("/api/v1/employees/", json=payload).json()
        assert body["bonus_pkr"] == 0.0
        assert body["deductions_pkr"] == 0.0


class TestListAndFilter:
    def test_only_this_company_s_employees_are_listed(self, client, db_session_factory) -> None:
        _seed_sync(db_session_factory)
        _seed_sync(db_session_factory, company_id=OTHER_COMPANY_ID)
        assert client.get("/api/v1/employees/").json()["total"] == 1

    def test_filter_by_department(self, client, db_session_factory) -> None:
        _seed_sync(db_session_factory, department="Finance")
        _seed_sync(db_session_factory, department="IT")
        body = client.get("/api/v1/employees/?department=IT").json()
        assert body["total"] == 1
        assert body["employees"][0]["department"] == "IT"

    def test_filter_by_active(self, client, db_session_factory) -> None:
        _seed_sync(db_session_factory, active=True)
        _seed_sync(db_session_factory, active=False)
        assert client.get("/api/v1/employees/?active=false").json()["total"] == 1

    def test_search_matches_name_case_insensitively(self, client, db_session_factory) -> None:
        _seed_sync(db_session_factory, name="Bilal Ahmed")
        _seed_sync(db_session_factory, name="Sana Malik")
        body = client.get("/api/v1/employees/?search=bilal").json()
        assert body["total"] == 1

    def test_payment_status_reflects_this_months_payroll_record(self, client, db_session_factory) -> None:
        employee_id = _seed_sync(db_session_factory)
        period = date.today().strftime("%Y-%m")
        async def _pay():
            async with db_session_factory() as session:
                session.add(PayrollRecord(
                    id=uuid.uuid4(), company_id=COMPANY_ID, employee_id=employee_id, period=period,
                    salary_pkr=100_000, bonus_pkr=0, deductions_pkr=0, net_salary_pkr=100_000,
                ))
                await session.commit()
        import asyncio
        asyncio.get_event_loop().run_until_complete(_pay())

        body = client.get("/api/v1/employees/").json()
        assert body["employees"][0]["payment_status"] == "paid"


class TestUpdate:
    def test_a_correction_updates_only_the_fields_sent(self, client, db_session_factory) -> None:
        employee_id = _seed_sync(db_session_factory, salary_pkr=100_000)
        response = client.put(f"/api/v1/employees/{employee_id}", json={"salary_pkr": 120_000})
        body = response.json()
        assert body["salary_pkr"] == 120_000
        assert body["net_salary_pkr"] == 120_000

    def test_updating_another_company_s_employee_is_a_404(self, client, db_session_factory) -> None:
        employee_id = _seed_sync(db_session_factory, company_id=OTHER_COMPANY_ID)
        assert client.put(f"/api/v1/employees/{employee_id}", json={"salary_pkr": 1}).status_code == 404

    def test_getting_a_missing_employee_is_a_404(self, client) -> None:
        assert client.get(f"/api/v1/employees/{uuid.uuid4()}").status_code == 404


class TestOptions:
    def test_suggested_departments_are_returned(self, client) -> None:
        assert "Finance" in client.get("/api/v1/employees/options").json()["departments"]

    def test_suggested_roles_are_returned(self, client) -> None:
        assert "Backend Developer" in client.get("/api/v1/employees/options").json()["roles"]


class TestDelete:
    def test_an_employee_with_no_payroll_history_is_deleted(self, client, db_session_factory) -> None:
        employee_id = _seed_sync(db_session_factory)
        response = client.delete(f"/api/v1/employees/{employee_id}")
        assert response.status_code == 204
        assert client.get(f"/api/v1/employees/{employee_id}").status_code == 404

    def test_an_employee_with_payroll_history_cannot_be_deleted(self, client, db_session_factory) -> None:
        employee_id = _seed_sync(db_session_factory)

        async def _pay():
            async with db_session_factory() as session:
                session.add(PayrollRecord(
                    id=uuid.uuid4(), company_id=COMPANY_ID, employee_id=employee_id, period="2026-08",
                    salary_pkr=100_000, bonus_pkr=0, deductions_pkr=0, net_salary_pkr=100_000,
                ))
                await session.commit()
        import asyncio
        asyncio.get_event_loop().run_until_complete(_pay())

        response = client.delete(f"/api/v1/employees/{employee_id}")
        assert response.status_code == 409
        assert client.get(f"/api/v1/employees/{employee_id}").status_code == 200

    def test_deleting_another_company_s_employee_is_a_404(self, client, db_session_factory) -> None:
        employee_id = _seed_sync(db_session_factory, company_id=OTHER_COMPANY_ID)
        assert client.delete(f"/api/v1/employees/{employee_id}").status_code == 404

    def test_deleting_a_missing_employee_is_a_404(self, client) -> None:
        assert client.delete(f"/api/v1/employees/{uuid.uuid4()}").status_code == 404
