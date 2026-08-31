"""Tax configuration — lazy get-or-create with the 18% GST default,
partial updates, tenancy scoping, and the filer_status enum."""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.tenancy import get_company_id
from app.db.session import get_db
from app.main import app
from app.models.base import Base

COMPANY_ID = uuid.uuid4()
OTHER_COMPANY_ID = uuid.uuid4()


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


class TestGetOrCreate:
    def test_first_read_defaults_to_eighteen_percent_gst(self, client) -> None:
        body = client.get("/api/v1/settings/tax").json()
        assert body["default_gst_rate"] == 18.0
        assert body["withholding_tax_rate"] == 0.0
        assert body["filer_status"] == "filer"


class TestUpdate:
    def test_the_gst_rate_can_be_changed(self, client) -> None:
        body = client.put("/api/v1/settings/tax", json={"default_gst_rate": 17.0}).json()
        assert body["default_gst_rate"] == 17.0

    def test_a_rate_above_one_hundred_is_rejected(self, client) -> None:
        assert client.put("/api/v1/settings/tax", json={"default_gst_rate": 150}).status_code == 422

    def test_filer_status_can_be_changed(self, client) -> None:
        body = client.put("/api/v1/settings/tax", json={"filer_status": "non_filer"}).json()
        assert body["filer_status"] == "non_filer"

    def test_an_unknown_filer_status_is_rejected(self, client) -> None:
        assert client.put("/api/v1/settings/tax", json={"filer_status": "bogus"}).status_code == 422

    def test_the_update_persists_across_reads(self, client) -> None:
        client.put("/api/v1/settings/tax", json={"withholding_tax_rate": 4.5})
        assert client.get("/api/v1/settings/tax").json()["withholding_tax_rate"] == 4.5


class TestTenancy:
    def test_companies_have_independent_tax_settings(self, client, db_session_factory) -> None:
        client.put("/api/v1/settings/tax", json={"default_gst_rate": 5.0})

        async def _db_other():
            async with db_session_factory() as session:
                yield session

        app.dependency_overrides[get_db] = _db_other
        app.dependency_overrides[get_company_id] = lambda: OTHER_COMPANY_ID
        other_client = TestClient(app)
        assert other_client.get("/api/v1/settings/tax").json()["default_gst_rate"] == 18.0
