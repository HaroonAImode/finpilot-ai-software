"""Vendor comparison — quotes scoped to a purchase request, tenancy
scoping, and the human-entered (never computed) score."""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.tenancy import get_company_id
from app.db.session import get_db
from app.main import app
from app.models import PurchaseRequest, PurchaseRequestStatus, VendorQuote
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


async def _seed_request(db_session_factory, **overrides) -> uuid.UUID:
    request = PurchaseRequest(
        id=uuid.uuid4(), company_id=overrides.pop("company_id", COMPANY_ID),
        item_description="Steel Sheets (2 tons)", department="Production", requester_name="Usman Tariq",
        amount_pkr=640_000.0, status=PurchaseRequestStatus.pending_approval,
    )
    async with db_session_factory() as session:
        session.add(request)
        await session.commit()
    return request.id


def _seed_request_sync(db_session_factory, **kwargs) -> uuid.UUID:
    import asyncio

    return asyncio.get_event_loop().run_until_complete(_seed_request(db_session_factory, **kwargs))


async def _seed_quote(db_session_factory, purchase_request_id, **overrides) -> uuid.UUID:
    quote = VendorQuote(
        id=uuid.uuid4(), company_id=overrides.pop("company_id", COMPANY_ID),
        purchase_request_id=purchase_request_id,
        vendor_name=overrides.pop("vendor_name", "ABC Traders"),
        price_pkr=overrides.pop("price_pkr", 312_000.0),
        **overrides,
    )
    async with db_session_factory() as session:
        session.add(quote)
        await session.commit()
    return quote.id


def _seed_quote_sync(db_session_factory, purchase_request_id, **kwargs) -> uuid.UUID:
    import asyncio

    return asyncio.get_event_loop().run_until_complete(_seed_quote(db_session_factory, purchase_request_id, **kwargs))


class TestCreate:
    def test_a_quote_is_created(self, client, db_session_factory) -> None:
        request_id = _seed_request_sync(db_session_factory)
        payload = {
            "purchase_request_id": str(request_id), "vendor_name": "ABC Traders", "price_pkr": 312_000,
            "delivery_estimate": "3 days", "quality_rating": "A+", "payment_terms": "Net 30",
        }
        response = client.post("/api/v1/procurement/vendor-comparison/", json=payload)
        assert response.status_code == 201
        assert response.json()["score"] is None

    def test_a_score_out_of_range_is_rejected(self, client, db_session_factory) -> None:
        request_id = _seed_request_sync(db_session_factory)
        payload = {"purchase_request_id": str(request_id), "vendor_name": "ABC Traders", "price_pkr": 1000, "score": 101}
        assert client.post("/api/v1/procurement/vendor-comparison/", json=payload).status_code == 422


class TestListAndFilter:
    def test_only_this_company_s_quotes_are_listed(self, client, db_session_factory) -> None:
        request_id = _seed_request_sync(db_session_factory)
        other_request_id = _seed_request_sync(db_session_factory, company_id=OTHER_COMPANY_ID)
        _seed_quote_sync(db_session_factory, request_id)
        _seed_quote_sync(db_session_factory, other_request_id, company_id=OTHER_COMPANY_ID)
        assert client.get("/api/v1/procurement/vendor-comparison/").json()["total"] == 1

    def test_scoped_to_one_purchase_request(self, client, db_session_factory) -> None:
        request_a = _seed_request_sync(db_session_factory)
        request_b = _seed_request_sync(db_session_factory)
        _seed_quote_sync(db_session_factory, request_a, vendor_name="A")
        _seed_quote_sync(db_session_factory, request_b, vendor_name="B")
        body = client.get(f"/api/v1/procurement/vendor-comparison/?purchase_request_id={request_a}").json()
        assert body["total"] == 1
        assert body["quotes"][0]["vendor_name"] == "A"

    def test_sorted_by_score_descending_then_price_ascending(self, client, db_session_factory) -> None:
        request_id = _seed_request_sync(db_session_factory)
        _seed_quote_sync(db_session_factory, request_id, vendor_name="Unscored", price_pkr=100)
        _seed_quote_sync(db_session_factory, request_id, vendor_name="Low score", score=50, price_pkr=200)
        _seed_quote_sync(db_session_factory, request_id, vendor_name="High score", score=90, price_pkr=300)
        names = [q["vendor_name"] for q in client.get("/api/v1/procurement/vendor-comparison/").json()["quotes"]]
        assert names == ["High score", "Low score", "Unscored"]


class TestUpdate:
    def test_a_quote_can_be_scored_after_the_fact(self, client, db_session_factory) -> None:
        request_id = _seed_request_sync(db_session_factory)
        quote_id = _seed_quote_sync(db_session_factory, request_id)
        response = client.put(f"/api/v1/procurement/vendor-comparison/{quote_id}", json={"score": 94})
        assert response.json()["score"] == 94

    def test_scoring_another_company_s_quote_is_a_404(self, client, db_session_factory) -> None:
        request_id = _seed_request_sync(db_session_factory, company_id=OTHER_COMPANY_ID)
        quote_id = _seed_quote_sync(db_session_factory, request_id, company_id=OTHER_COMPANY_ID)
        assert client.put(f"/api/v1/procurement/vendor-comparison/{quote_id}", json={"score": 50}).status_code == 404
