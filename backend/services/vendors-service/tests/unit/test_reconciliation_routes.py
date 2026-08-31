"""Vendor reconciliation — the accountant's queue for turning raw OCR
vendor_name strings into real, deduplicated vendors. Invoice Service is
patched throughout, same as test_vendor_routes.py: this suite is about
this service's own decisions (matching, ignoring, linking), not about
another service being reachable.
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


def _group(vendor_name: str, count: int = 1, total: float = 1000.0) -> dict:
    return {
        "vendor_name": vendor_name,
        "invoice_ids": [str(uuid.uuid4()) for _ in range(count)],
        "invoice_count": count,
        "total_amount": total,
        "sample_invoice_number": "INV-1",
        "latest_invoice_date": "2026-08-01",
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


async def _seed_vendor(db_session_factory, **overrides) -> uuid.UUID:
    vendor = Vendor(
        id=uuid.uuid4(), company_id=overrides.pop("company_id", COMPANY_ID),
        name=overrides.pop("name", "Seeded Vendor"),
        status=overrides.pop("status", VendorStatus.active),
        **overrides,
    )
    async with db_session_factory() as session:
        session.add(vendor)
        await session.commit()
    return vendor.id


def _seed_vendor_sync(db_session_factory, **kwargs) -> uuid.UUID:
    import asyncio

    return asyncio.get_event_loop().run_until_complete(_seed_vendor(db_session_factory, **kwargs))


class TestQueue:
    def test_groups_come_back_with_match_suggestions(self, client, db_session_factory) -> None:
        _seed_vendor_sync(db_session_factory, name="ABC Traders")
        with patch(
            "app.api.routes.reconciliation.fetch_vendor_groups",
            AsyncMock(return_value=[_group("ABC Tradres")]),  # a plausible OCR misread
        ):
            body = client.get("/api/v1/vendors/reconciliation/queue").json()
        assert body["groups"][0]["vendor_name"] == "ABC Tradres"
        assert body["groups"][0]["suggestions"][0]["name"] == "ABC Traders"

    def test_a_name_with_no_similar_vendor_gets_no_suggestions(self, client, db_session_factory) -> None:
        _seed_vendor_sync(db_session_factory, name="ABC Traders")
        with patch(
            "app.api.routes.reconciliation.fetch_vendor_groups",
            AsyncMock(return_value=[_group("Completely Different Supplier Co")]),
        ):
            body = client.get("/api/v1/vendors/reconciliation/queue").json()
        assert body["groups"][0]["suggestions"] == []

    def test_ignored_names_are_removed_from_the_queue(self, client, db_session_factory) -> None:
        client.post("/api/v1/vendors/reconciliation/ignore", json={"vendor_name": "Receipt"})
        with patch(
            "app.api.routes.reconciliation.fetch_vendor_groups",
            AsyncMock(return_value=[_group("Receipt"), _group("Real Supplier")]),
        ):
            body = client.get("/api/v1/vendors/reconciliation/queue").json()
        names = [g["vendor_name"] for g in body["groups"]]
        assert "Receipt" not in names
        assert "Real Supplier" in names
        assert body["ignored_count"] == 1

    def test_a_downstream_failure_is_a_502_not_an_empty_queue(self, client) -> None:
        """The queue *is* Invoice Service's data — an empty result would
        tell the accountant there is nothing left to reconcile, which is
        not a claim we can make when we simply could not ask."""
        with patch(
            "app.api.routes.reconciliation.fetch_vendor_groups",
            AsyncMock(side_effect=InvoiceServiceError("down")),
        ):
            response = client.get("/api/v1/vendors/reconciliation/queue")
        assert response.status_code == 502


class TestLinkToExistingVendor:
    def test_links_the_whole_group_in_one_call(self, client, db_session_factory) -> None:
        vendor_id = _seed_vendor_sync(db_session_factory, name="ABC Traders")
        ids = [str(uuid.uuid4()), str(uuid.uuid4())]
        with patch(
            "app.api.routes.reconciliation.bulk_link_vendor", AsyncMock(return_value=2),
        ) as mock_link:
            response = client.post(
                "/api/v1/vendors/reconciliation/link",
                json={"vendor_name": "ABC Tradres", "invoice_ids": ids, "vendor_id": str(vendor_id)},
            )
        assert response.status_code == 200
        assert response.json()["linked_count"] == 2
        mock_link.assert_awaited_once()

    def test_linking_to_a_nonexistent_vendor_is_a_404(self, client) -> None:
        response = client.post(
            "/api/v1/vendors/reconciliation/link",
            json={"vendor_name": "X", "invoice_ids": [str(uuid.uuid4())], "vendor_id": str(uuid.uuid4())},
        )
        assert response.status_code == 404

    def test_linking_to_another_company_s_vendor_is_a_404(self, client, db_session_factory) -> None:
        vendor_id = _seed_vendor_sync(db_session_factory, name="Theirs", company_id=uuid.uuid4())
        response = client.post(
            "/api/v1/vendors/reconciliation/link",
            json={"vendor_name": "X", "invoice_ids": [str(uuid.uuid4())], "vendor_id": str(vendor_id)},
        )
        assert response.status_code == 404


class TestCreateAndLink:
    def test_creates_the_vendor_and_links_in_one_step(self, client) -> None:
        with patch(
            "app.api.routes.reconciliation.bulk_link_vendor", AsyncMock(return_value=3),
        ):
            response = client.post(
                "/api/v1/vendors/reconciliation/create-and-link",
                json={
                    "vendor_name": "Karachi Steel Co", "invoice_ids": [str(uuid.uuid4())],
                    "name": "Karachi Steel Co", "category": "Steel",
                },
            )
        assert response.status_code == 201
        body = response.json()
        assert body["linked_count"] == 3
        # The vendor is now a real, listable row — not a side effect only
        # visible through the reconciliation response.
        listed = client.get("/api/v1/vendors/").json()
        assert any(v["name"] == "Karachi Steel Co" for v in listed["vendors"])

    def test_duplicate_name_is_a_409(self, client, db_session_factory) -> None:
        _seed_vendor_sync(db_session_factory, name="ABC Traders")
        response = client.post(
            "/api/v1/vendors/reconciliation/create-and-link",
            json={"vendor_name": "ABC Traders", "invoice_ids": [str(uuid.uuid4())], "name": "ABC Traders"},
        )
        assert response.status_code == 409

    def test_the_vendor_survives_a_downstream_link_failure(self, client, db_session_factory) -> None:
        """A partial success (vendor created, link failed) is a real, valid
        state — not something to roll back and hide."""
        with patch(
            "app.api.routes.reconciliation.bulk_link_vendor",
            AsyncMock(side_effect=InvoiceServiceError("down")),
        ):
            response = client.post(
                "/api/v1/vendors/reconciliation/create-and-link",
                json={"vendor_name": "New Co", "invoice_ids": [str(uuid.uuid4())], "name": "New Co"},
            )
        assert response.status_code == 502
        listed = client.get("/api/v1/vendors/").json()
        assert any(v["name"] == "New Co" for v in listed["vendors"])


class TestIgnore:
    def test_an_ignored_name_can_be_listed_and_undone(self, client) -> None:
        created = client.post(
            "/api/v1/vendors/reconciliation/ignore", json={"vendor_name": "Receipt"},
        ).json()
        assert client.get("/api/v1/vendors/reconciliation/ignored").json() == [
            {"id": created["id"], "vendor_name": "Receipt"}
        ]
        assert client.delete(f"/api/v1/vendors/reconciliation/ignored/{created['id']}").status_code == 204
        assert client.get("/api/v1/vendors/reconciliation/ignored").json() == []

    def test_ignoring_the_same_name_twice_is_a_409(self, client) -> None:
        client.post("/api/v1/vendors/reconciliation/ignore", json={"vendor_name": "Receipt"})
        response = client.post("/api/v1/vendors/reconciliation/ignore", json={"vendor_name": "Receipt"})
        assert response.status_code == 409

    def test_unignoring_a_nonexistent_row_is_a_404(self, client) -> None:
        assert client.delete(f"/api/v1/vendors/reconciliation/ignored/{uuid.uuid4()}").status_code == 404
