"""Purchase orders — creation-from-an-approved-request guard, status
updates, tenancy scoping, and the computed `delayed` flag (in_transit
past expected_delivery, never a stored status — see PurchaseOrderStatus's
own docstring)."""
import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.tenancy import get_company_id
from app.db.session import get_db
from app.main import app
from app.models import PurchaseOrder, PurchaseOrderStatus, PurchaseRequest, PurchaseRequestStatus
from app.models.base import Base

COMPANY_ID = uuid.uuid4()
OTHER_COMPANY_ID = uuid.uuid4()
TODAY = date.today()


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


async def _seed_request(db_session_factory, **overrides) -> uuid.UUID:
    request = PurchaseRequest(
        id=uuid.uuid4(), company_id=overrides.pop("company_id", COMPANY_ID),
        item_description="Item", department="Production", requester_name="Someone",
        amount_pkr=10_000.0, status=overrides.pop("status", PurchaseRequestStatus.approved),
    )
    async with db_session_factory() as session:
        session.add(request)
        await session.commit()
    return request.id


def _seed_request_sync(db_session_factory, **kwargs) -> uuid.UUID:
    import asyncio

    return asyncio.get_event_loop().run_until_complete(_seed_request(db_session_factory, **kwargs))


async def _seed_order(db_session_factory, **overrides) -> uuid.UUID:
    order = PurchaseOrder(
        id=uuid.uuid4(), company_id=overrides.pop("company_id", COMPANY_ID),
        purchase_request_id=overrides.pop("purchase_request_id", None),
        vendor_name=overrides.pop("vendor_name", "Karachi Steel Co."),
        order_date=overrides.pop("order_date", TODAY),
        amount_pkr=overrides.pop("amount_pkr", 100_000.0),
        expected_delivery=overrides.pop("expected_delivery", TODAY + timedelta(days=7)),
        status=overrides.pop("status", PurchaseOrderStatus.in_transit),
        **overrides,
    )
    async with db_session_factory() as session:
        session.add(order)
        await session.commit()
    return order.id


def _seed_order_sync(db_session_factory, **kwargs) -> uuid.UUID:
    import asyncio

    return asyncio.get_event_loop().run_until_complete(_seed_order(db_session_factory, **kwargs))


VALID = {"vendor_name": "Karachi Steel Co.", "amount_pkr": 980_000, "expected_delivery": (TODAY + timedelta(days=7)).isoformat()}


class TestCreate:
    def test_an_order_is_created_without_a_request(self, client) -> None:
        response = client.post("/api/v1/procurement/orders/", json=VALID)
        assert response.status_code == 201
        assert response.json()["status"] == "in_transit"
        assert response.json()["order_date"] == TODAY.isoformat()

    def test_an_order_can_be_created_from_an_approved_request(self, client, db_session_factory) -> None:
        request_id = _seed_request_sync(db_session_factory, status=PurchaseRequestStatus.approved)
        payload = {**VALID, "purchase_request_id": str(request_id)}
        assert client.post("/api/v1/procurement/orders/", json=payload).status_code == 201

    def test_a_pending_request_cannot_become_an_order(self, client, db_session_factory) -> None:
        request_id = _seed_request_sync(db_session_factory, status=PurchaseRequestStatus.pending_approval)
        payload = {**VALID, "purchase_request_id": str(request_id)}
        assert client.post("/api/v1/procurement/orders/", json=payload).status_code == 409

    def test_a_missing_request_id_is_a_404(self, client) -> None:
        payload = {**VALID, "purchase_request_id": str(uuid.uuid4())}
        assert client.post("/api/v1/procurement/orders/", json=payload).status_code == 404

    def test_a_zero_amount_is_rejected(self, client) -> None:
        assert client.post("/api/v1/procurement/orders/", json={**VALID, "amount_pkr": 0}).status_code == 422


class TestListAndFilter:
    def test_only_this_company_s_orders_are_listed(self, client, db_session_factory) -> None:
        _seed_order_sync(db_session_factory)
        _seed_order_sync(db_session_factory, company_id=OTHER_COMPANY_ID)
        assert client.get("/api/v1/procurement/orders/").json()["total"] == 1

    def test_filter_by_status(self, client, db_session_factory) -> None:
        _seed_order_sync(db_session_factory, status=PurchaseOrderStatus.delivered)
        _seed_order_sync(db_session_factory, status=PurchaseOrderStatus.in_transit)
        assert client.get("/api/v1/procurement/orders/?status=delivered").json()["total"] == 1

    def test_an_unknown_status_filter_is_a_clear_400(self, client) -> None:
        assert client.get("/api/v1/procurement/orders/?status=bogus").status_code == 400


class TestDelayedFlag:
    def test_in_transit_past_expected_delivery_is_delayed(self, client, db_session_factory) -> None:
        order_id = _seed_order_sync(
            db_session_factory, status=PurchaseOrderStatus.in_transit, expected_delivery=TODAY - timedelta(days=1),
        )
        assert client.get(f"/api/v1/procurement/orders/{order_id}").json()["delayed"] is True

    def test_in_transit_before_expected_delivery_is_not_delayed(self, client, db_session_factory) -> None:
        order_id = _seed_order_sync(
            db_session_factory, status=PurchaseOrderStatus.in_transit, expected_delivery=TODAY + timedelta(days=1),
        )
        assert client.get(f"/api/v1/procurement/orders/{order_id}").json()["delayed"] is False

    def test_a_delivered_order_past_its_date_is_not_delayed(self, client, db_session_factory) -> None:
        """Delayed only describes something still in flight."""
        order_id = _seed_order_sync(
            db_session_factory, status=PurchaseOrderStatus.delivered, expected_delivery=TODAY - timedelta(days=30),
        )
        assert client.get(f"/api/v1/procurement/orders/{order_id}").json()["delayed"] is False


class TestUpdateStatus:
    def test_status_transitions_to_delivered(self, client, db_session_factory) -> None:
        order_id = _seed_order_sync(db_session_factory)
        response = client.put(f"/api/v1/procurement/orders/{order_id}/status", json={"status": "delivered"})
        assert response.json()["status"] == "delivered"

    def test_an_unknown_status_is_a_clear_400(self, client, db_session_factory) -> None:
        order_id = _seed_order_sync(db_session_factory)
        assert client.put(f"/api/v1/procurement/orders/{order_id}/status", json={"status": "bogus"}).status_code == 400

    def test_updating_another_company_s_order_is_a_404(self, client, db_session_factory) -> None:
        order_id = _seed_order_sync(db_session_factory, company_id=OTHER_COMPANY_ID)
        response = client.put(f"/api/v1/procurement/orders/{order_id}/status", json={"status": "delivered"})
        assert response.status_code == 404
