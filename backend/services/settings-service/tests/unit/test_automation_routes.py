"""AI automation toggles — lazy get-or-create with all-enabled defaults,
partial updates, tenancy scoping."""
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
    def test_first_read_defaults_everything_on(self, client) -> None:
        body = client.get("/api/v1/settings/automation").json()
        assert body["auto_categorize_expenses"] is True
        assert body["auto_detect_duplicates"] is True
        assert body["auto_insights"] is True
        assert body["smart_vendor_suggestions"] is True
        assert body["enabled_ai"] is True


class TestUpdate:
    def test_a_single_toggle_can_be_turned_off(self, client) -> None:
        body = client.put("/api/v1/settings/automation", json={"enabled_ai": False}).json()
        assert body["enabled_ai"] is False
        assert body["auto_insights"] is True

    def test_the_update_persists_across_reads(self, client) -> None:
        client.put("/api/v1/settings/automation", json={"smart_vendor_suggestions": False})
        assert client.get("/api/v1/settings/automation").json()["smart_vendor_suggestions"] is False


class TestTenancy:
    def test_companies_have_independent_settings(self, client, db_session_factory) -> None:
        client.put("/api/v1/settings/automation", json={"enabled_ai": False})

        async def _db_other():
            async with db_session_factory() as session:
                yield session

        app.dependency_overrides[get_db] = _db_other
        app.dependency_overrides[get_company_id] = lambda: OTHER_COMPANY_ID
        other_client = TestClient(app)
        assert other_client.get("/api/v1/settings/automation").json()["enabled_ai"] is True
