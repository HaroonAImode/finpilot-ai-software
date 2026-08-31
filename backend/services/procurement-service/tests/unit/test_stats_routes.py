"""Dashboard stats — pending (requests awaiting approval), completed/
cancelled (order counts), and the computed delayed count."""
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


async def _seed(db_session_factory, request=None, order=None) -> None:
    async with db_session_factory() as session:
        if request is not None:
            session.add(PurchaseRequest(
                id=uuid.uuid4(), company_id=COMPANY_ID, item_description="Item", department="Production",
                requester_name="Someone", amount_pkr=request.get("amount_pkr", 10_000.0),
                status=request["status"],
            ))
        if order is not None:
            session.add(PurchaseOrder(
                id=uuid.uuid4(), company_id=COMPANY_ID, vendor_name="Vendor",
                order_date=TODAY, amount_pkr=order.get("amount_pkr", 10_000.0),
                expected_delivery=order.get("expected_delivery", TODAY), status=order["status"],
            ))
        await session.commit()


def _seed_sync(db_session_factory, **kwargs) -> None:
    import asyncio

    asyncio.get_event_loop().run_until_complete(_seed(db_session_factory, **kwargs))


def test_pending_counts_requests_awaiting_approval(client, db_session_factory) -> None:
    _seed_sync(db_session_factory, request={"status": PurchaseRequestStatus.pending_approval, "amount_pkr": 640_000})
    _seed_sync(db_session_factory, request={"status": PurchaseRequestStatus.approved, "amount_pkr": 999_999})
    body = client.get("/api/v1/procurement/stats").json()
    assert body["pending_count"] == 1
    assert body["pending_amount_pkr"] == 640_000.0


def test_completed_counts_delivered_orders(client, db_session_factory) -> None:
    _seed_sync(db_session_factory, order={"status": PurchaseOrderStatus.delivered})
    _seed_sync(db_session_factory, order={"status": PurchaseOrderStatus.in_transit})
    assert client.get("/api/v1/procurement/stats").json()["completed_count"] == 1


def test_cancelled_counts_cancelled_orders(client, db_session_factory) -> None:
    _seed_sync(db_session_factory, order={"status": PurchaseOrderStatus.cancelled})
    assert client.get("/api/v1/procurement/stats").json()["cancelled_count"] == 1


def test_delayed_counts_in_transit_past_expected_delivery(client, db_session_factory) -> None:
    _seed_sync(db_session_factory, order={
        "status": PurchaseOrderStatus.in_transit, "expected_delivery": TODAY - timedelta(days=5),
    })
    _seed_sync(db_session_factory, order={
        "status": PurchaseOrderStatus.in_transit, "expected_delivery": TODAY + timedelta(days=5),
    })
    assert client.get("/api/v1/procurement/stats").json()["delayed_count"] == 1


def test_an_empty_company_is_all_zeros(client) -> None:
    body = client.get("/api/v1/procurement/stats").json()
    assert body == {
        "pending_count": 0, "pending_amount_pkr": 0.0, "completed_count": 0,
        "delayed_count": 0, "cancelled_count": 0,
    }
