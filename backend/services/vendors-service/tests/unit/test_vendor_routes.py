"""Vendors — CRUD, tenancy scoping, and the two behaviours that make the
cross-service split safe: spend degrading rather than lying, and a vendor's
invoices failing loudly rather than looking empty.

Invoice Service is patched throughout — this suite is about *this* service's
behaviour, not about another one being reachable.
"""
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.tenancy import get_company_id
from app.db.session import get_db
from app.main import app
from app.models import Vendor, VendorStatus
from app.models.base import Base
from app.services.invoice_service_client import InvoiceServiceError

COMPANY_ID = uuid.uuid4()
OTHER_COMPANY_ID = uuid.uuid4()

VALID = {
    "name": "ABC Traders", "category": "Raw Material", "city": "Karachi",
    "ntn": "3947261-8", "payment_terms": "Net 30", "rating": 4.8, "status": "active",
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
def no_spend():
    """Default: Invoice Service reachable, reporting no linked invoices."""
    with patch("app.api.routes.vendors.fetch_vendor_spend", AsyncMock(return_value={})) as mock:
        yield mock


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
    vendor = Vendor(
        id=uuid.uuid4(),
        company_id=overrides.pop("company_id", COMPANY_ID),
        name=overrides.pop("name", "Seeded Vendor"),
        status=overrides.pop("status", VendorStatus.active),
        **overrides,
    )
    async with db_session_factory() as session:
        session.add(vendor)
        await session.commit()
    return vendor.id


def _seed_sync(db_session_factory, **kwargs) -> uuid.UUID:
    import asyncio

    return asyncio.get_event_loop().run_until_complete(_seed(db_session_factory, **kwargs))


class TestCreate:
    def test_a_vendor_is_created(self, client, no_spend) -> None:
        response = client.post("/api/v1/vendors/", json=VALID)
        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "ABC Traders"
        assert body["status"] == "active"
        # Nothing linked yet, so spend is definitionally zero — not unknown.
        assert body["total_spend_pkr"] == 0.0
        assert body["spend_unavailable"] is False

    def test_a_duplicate_name_in_the_same_company_is_a_clear_409(self, client, no_spend) -> None:
        """A duplicate vendor is exactly what this service exists to prevent,
        so the caller is told plainly rather than getting a 500."""
        client.post("/api/v1/vendors/", json=VALID)
        response = client.post("/api/v1/vendors/", json=VALID)
        assert response.status_code == 409
        assert "already exists" in response.json()["detail"]

    def test_a_blank_name_is_rejected(self, client, no_spend) -> None:
        assert client.post("/api/v1/vendors/", json={**VALID, "name": "   "}).status_code == 422

    def test_a_rating_above_five_is_rejected(self, client, no_spend) -> None:
        assert client.post("/api/v1/vendors/", json={**VALID, "rating": 6}).status_code == 422

    def test_rating_may_be_omitted(self, client, no_spend) -> None:
        """Unrated and rated-zero are different statements about a supplier."""
        payload = {k: v for k, v in VALID.items() if k != "rating"}
        assert client.post("/api/v1/vendors/", json=payload).json()["rating"] is None


class TestListAndFilter:
    def test_only_this_company_s_vendors_are_listed(self, client, db_session_factory, no_spend) -> None:
        _seed_sync(db_session_factory, name="Mine")
        _seed_sync(db_session_factory, name="Theirs", company_id=OTHER_COMPANY_ID)
        body = client.get("/api/v1/vendors/").json()
        assert body["total"] == 1
        assert body["vendors"][0]["name"] == "Mine"

    def test_status_filter(self, client, db_session_factory, no_spend) -> None:
        _seed_sync(db_session_factory, name="Active One")
        _seed_sync(db_session_factory, name="Under Review", status=VendorStatus.review)
        assert client.get("/api/v1/vendors/?status=review").json()["total"] == 1

    def test_an_unknown_status_filter_is_rejected(self, client, no_spend) -> None:
        assert client.get("/api/v1/vendors/?status=nonsense").status_code == 400

    def test_search_matches_case_insensitively(self, client, db_session_factory, no_spend) -> None:
        _seed_sync(db_session_factory, name="Karachi Steel Co.")
        assert client.get("/api/v1/vendors/?search=karachi").json()["total"] == 1


class TestSpendIsDerived:
    def test_spend_comes_from_invoice_service(self, client, db_session_factory) -> None:
        vendor_id = _seed_sync(db_session_factory, name="ABC")
        with patch(
            "app.api.routes.vendors.fetch_vendor_spend",
            AsyncMock(return_value={vendor_id: (1_240_000.0, 7)}),
        ):
            row = client.get("/api/v1/vendors/").json()["vendors"][0]
        assert row["total_spend_pkr"] == 1_240_000.0
        assert row["invoice_count"] == 7

    def test_the_list_still_works_when_invoice_service_is_down(self, client, db_session_factory) -> None:
        """Degrades rather than 502s: a vendor directory without spend is
        still useful, and failing the whole list over one downstream
        aggregate would be worse."""
        _seed_sync(db_session_factory, name="ABC")
        with patch(
            "app.api.routes.vendors.fetch_vendor_spend",
            AsyncMock(side_effect=InvoiceServiceError("down")),
        ):
            response = client.get("/api/v1/vendors/")
        assert response.status_code == 200
        row = response.json()["vendors"][0]
        # The flag is what stops the UI showing a real vendor's spend as a
        # confident 0.00 — which would be a lie, not a gap.
        assert row["spend_unavailable"] is True
        assert row["total_spend_pkr"] == 0.0


class TestTopVendors:
    def test_sorted_by_spend_descending(self, client, db_session_factory) -> None:
        low = _seed_sync(db_session_factory, name="Low")
        high = _seed_sync(db_session_factory, name="High")
        with patch(
            "app.api.routes.vendors.fetch_vendor_spend",
            AsyncMock(return_value={low: (100.0, 1), high: (900.0, 2)}),
        ):
            rows = client.get("/api/v1/vendors/top").json()
        assert [r["name"] for r in rows] == ["High", "Low"]

    def test_limit_is_respected(self, client, db_session_factory, no_spend) -> None:
        for i in range(4):
            _seed_sync(db_session_factory, name=f"Vendor {i}")
        assert len(client.get("/api/v1/vendors/top?limit=2").json()) == 2

    def test_top_is_not_parsed_as_a_vendor_id(self, client, no_spend) -> None:
        """The literal path must match before /{vendor_id}, which would
        otherwise try to read "top" as a UUID."""
        assert client.get("/api/v1/vendors/top").status_code == 200

    def test_options_is_not_parsed_as_a_vendor_id(self, client) -> None:
        response = client.get("/api/v1/vendors/options")
        assert response.status_code == 200
        assert "Raw Material" in response.json()["categories"]


class TestUpdate:
    def test_only_supplied_fields_change(self, client, db_session_factory, no_spend) -> None:
        vendor_id = _seed_sync(db_session_factory, name="Original", city="Karachi")
        body = client.put(f"/api/v1/vendors/{vendor_id}", json={"rating": 3.5}).json()
        assert body["rating"] == 3.5
        assert body["name"] == "Original"
        assert body["city"] == "Karachi"

    def test_status_can_be_moved_to_review(self, client, db_session_factory, no_spend) -> None:
        vendor_id = _seed_sync(db_session_factory, name="Suspect Co")
        assert client.put(f"/api/v1/vendors/{vendor_id}", json={"status": "review"}).json()["status"] == "review"

    def test_another_company_s_vendor_cannot_be_updated(self, client, db_session_factory, no_spend) -> None:
        vendor_id = _seed_sync(db_session_factory, name="Theirs", company_id=OTHER_COMPANY_ID)
        assert client.put(f"/api/v1/vendors/{vendor_id}", json={"rating": 1}).status_code == 404

    def test_renaming_onto_an_existing_name_is_a_409(self, client, db_session_factory, no_spend) -> None:
        _seed_sync(db_session_factory, name="Taken")
        vendor_id = _seed_sync(db_session_factory, name="Free")
        assert client.put(f"/api/v1/vendors/{vendor_id}", json={"name": "Taken"}).status_code == 409


class TestVendorInvoices:
    def test_invoices_are_proxied_from_invoice_service(self, client, db_session_factory) -> None:
        vendor_id = _seed_sync(db_session_factory, name="ABC")
        payload = {"invoices": [{"id": str(uuid.uuid4())}], "total": 1, "skip": 0, "limit": 20}
        with patch("app.api.routes.vendors.fetch_vendor_invoices", AsyncMock(return_value=payload)):
            response = client.get(f"/api/v1/vendors/{vendor_id}/invoices")
        assert response.status_code == 200
        assert response.json()["total"] == 1

    def test_a_downstream_failure_is_a_502_not_an_empty_list(self, client, db_session_factory) -> None:
        """Unlike spend, the whole response *is* the downstream data —
        degrading would assert "this vendor has no invoices", which we
        cannot claim when we simply could not ask."""
        vendor_id = _seed_sync(db_session_factory, name="ABC")
        with patch(
            "app.api.routes.vendors.fetch_vendor_invoices",
            AsyncMock(side_effect=InvoiceServiceError("Invoice Service is down")),
        ):
            response = client.get(f"/api/v1/vendors/{vendor_id}/invoices")
        assert response.status_code == 502
        assert "down" in response.json()["detail"]

    def test_another_company_s_vendor_404s_before_calling_downstream(
        self, client, db_session_factory,
    ) -> None:
        vendor_id = _seed_sync(db_session_factory, name="Theirs", company_id=OTHER_COMPANY_ID)
        proxy = AsyncMock()
        with patch("app.api.routes.vendors.fetch_vendor_invoices", proxy):
            assert client.get(f"/api/v1/vendors/{vendor_id}/invoices").status_code == 404
        proxy.assert_not_awaited()
