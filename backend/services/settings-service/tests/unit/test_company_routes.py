"""Company profile — lazy get-or-create, partial updates, tenancy scoping."""
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
    def test_first_read_returns_defaults(self, client) -> None:
        body = client.get("/api/v1/settings/company").json()
        assert body["name"] is None
        assert body["ntn"] is None

    def test_branding_defaults(self, client) -> None:
        body = client.get("/api/v1/settings/company").json()
        assert body["logo_placement"] == "left"
        assert body["invoice_template"] == "classic"
        assert body["logo_url"] is None

    def test_name_is_not_copied_from_anywhere(self, client) -> None:
        """Deliberately not synchronised with Auth Service's company_name
        — see the model's own docstring."""
        assert client.get("/api/v1/settings/company").json()["name"] is None


class TestUpdate:
    def test_a_field_can_be_set(self, client) -> None:
        response = client.put("/api/v1/settings/company", json={"ntn": "3947261-8", "city": "Karachi"})
        body = response.json()
        assert body["ntn"] == "3947261-8"
        assert body["city"] == "Karachi"

    def test_only_fields_sent_are_applied(self, client) -> None:
        client.put("/api/v1/settings/company", json={"city": "Karachi"})
        response = client.put("/api/v1/settings/company", json={"ntn": "3947261-8"})
        body = response.json()
        assert body["ntn"] == "3947261-8"
        assert body["city"] == "Karachi"

    def test_the_update_persists_across_reads(self, client) -> None:
        client.put("/api/v1/settings/company", json={"name": "Khan Enterprises"})
        assert client.get("/api/v1/settings/company").json()["name"] == "Khan Enterprises"

    def test_a_logo_data_uri_can_be_stored(self, client) -> None:
        data_uri = "data:image/png;base64," + ("A" * 5000)  # well past the old 1000-char column limit
        body = client.put("/api/v1/settings/company", json={"logo_url": data_uri}).json()
        assert body["logo_url"] == data_uri

    def test_logo_placement_can_be_changed(self, client) -> None:
        body = client.put("/api/v1/settings/company", json={"logo_placement": "center"}).json()
        assert body["logo_placement"] == "center"

    def test_an_unknown_logo_placement_is_rejected(self, client) -> None:
        assert client.put("/api/v1/settings/company", json={"logo_placement": "bogus"}).status_code == 422

    def test_invoice_template_can_be_changed(self, client) -> None:
        body = client.put("/api/v1/settings/company", json={"invoice_template": "midnight"}).json()
        assert body["invoice_template"] == "midnight"

    def test_an_unknown_invoice_template_is_rejected(self, client) -> None:
        assert client.put("/api/v1/settings/company", json={"invoice_template": "bogus"}).status_code == 422


class TestTenancy:
    def test_companies_have_independent_profiles(self, client, db_session_factory) -> None:
        client.put("/api/v1/settings/company", json={"name": "Mine"})

        async def _db_other():
            async with db_session_factory() as session:
                yield session

        app.dependency_overrides[get_db] = _db_other
        app.dependency_overrides[get_company_id] = lambda: OTHER_COMPANY_ID
        other_client = TestClient(app)
        assert other_client.get("/api/v1/settings/company").json()["name"] is None
