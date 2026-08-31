"""Purchase requests — CRUD, tenancy scoping, the approve/reject lifecycle,
and the derived timeline (built from the request's own lifecycle plus any
linked PurchaseOrder, never a separately-authored steps table)."""
import uuid

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

VALID = {
    "item_description": "Steel Sheets (2 tons)", "department": "Production",
    "requester_name": "Usman Tariq", "amount_pkr": 640_000,
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
    request = PurchaseRequest(
        id=uuid.uuid4(), company_id=overrides.pop("company_id", COMPANY_ID),
        item_description=overrides.pop("item_description", "Item"),
        department=overrides.pop("department", "Production"),
        requester_name=overrides.pop("requester_name", "Someone"),
        amount_pkr=overrides.pop("amount_pkr", 10_000.0),
        status=overrides.pop("status", PurchaseRequestStatus.pending_approval),
        **overrides,
    )
    async with db_session_factory() as session:
        session.add(request)
        await session.commit()
    return request.id


def _seed_sync(db_session_factory, **kwargs) -> uuid.UUID:
    import asyncio

    return asyncio.get_event_loop().run_until_complete(_seed(db_session_factory, **kwargs))


class TestCreate:
    def test_a_request_is_created_pending(self, client) -> None:
        response = client.post("/api/v1/procurement/requests/", json=VALID)
        assert response.status_code == 201
        assert response.json()["status"] == "pending_approval"

    def test_a_blank_item_description_is_rejected(self, client) -> None:
        assert client.post("/api/v1/procurement/requests/", json={**VALID, "item_description": "  "}).status_code == 422

    def test_a_zero_amount_is_rejected(self, client) -> None:
        assert client.post("/api/v1/procurement/requests/", json={**VALID, "amount_pkr": 0}).status_code == 422


class TestListAndFilter:
    def test_only_this_company_s_requests_are_listed(self, client, db_session_factory) -> None:
        _seed_sync(db_session_factory)
        _seed_sync(db_session_factory, company_id=OTHER_COMPANY_ID)
        assert client.get("/api/v1/procurement/requests/").json()["total"] == 1

    def test_filter_by_status(self, client, db_session_factory) -> None:
        _seed_sync(db_session_factory, status=PurchaseRequestStatus.approved)
        _seed_sync(db_session_factory, status=PurchaseRequestStatus.pending_approval)
        body = client.get("/api/v1/procurement/requests/?status=approved").json()
        assert body["total"] == 1

    def test_an_unknown_status_filter_is_a_clear_400(self, client) -> None:
        assert client.get("/api/v1/procurement/requests/?status=bogus").status_code == 400

    def test_search_matches_item_description(self, client, db_session_factory) -> None:
        _seed_sync(db_session_factory, item_description="Office Laptops (4)")
        _seed_sync(db_session_factory, item_description="Diesel Refill")
        body = client.get("/api/v1/procurement/requests/?search=laptop").json()
        assert body["total"] == 1


class TestApproveReject:
    def test_approve_transitions_status(self, client, db_session_factory) -> None:
        request_id = _seed_sync(db_session_factory)
        assert client.post(f"/api/v1/procurement/requests/{request_id}/approve").json()["status"] == "approved"

    def test_reject_transitions_status(self, client, db_session_factory) -> None:
        request_id = _seed_sync(db_session_factory)
        assert client.post(f"/api/v1/procurement/requests/{request_id}/reject").json()["status"] == "rejected"

    def test_an_already_decided_request_cannot_be_approved_again(self, client, db_session_factory) -> None:
        request_id = _seed_sync(db_session_factory, status=PurchaseRequestStatus.approved)
        assert client.post(f"/api/v1/procurement/requests/{request_id}/approve").status_code == 409

    def test_approving_another_company_s_request_is_a_404(self, client, db_session_factory) -> None:
        request_id = _seed_sync(db_session_factory, company_id=OTHER_COMPANY_ID)
        assert client.post(f"/api/v1/procurement/requests/{request_id}/approve").status_code == 404


class TestOptions:
    def test_suggested_departments_are_returned(self, client) -> None:
        assert "Finance" in client.get("/api/v1/procurement/requests/options").json()["departments"]

    def test_options_is_not_parsed_as_a_request_id(self, client) -> None:
        assert client.get("/api/v1/procurement/requests/options").status_code == 200


class TestTimeline:
    def test_a_pending_request_has_two_steps_done(self, client, db_session_factory) -> None:
        request_id = _seed_sync(db_session_factory)
        steps = client.get(f"/api/v1/procurement/requests/{request_id}/timeline").json()["steps"]
        assert [s["completed"] for s in steps] == [True, False, False]
        assert steps[1]["detail"] == "Awaiting approval"

    def test_an_approved_request_with_no_order_yet(self, client, db_session_factory) -> None:
        request_id = _seed_sync(db_session_factory, status=PurchaseRequestStatus.approved)
        steps = client.get(f"/api/v1/procurement/requests/{request_id}/timeline").json()["steps"]
        assert steps[1]["completed"] is True
        assert steps[1]["detail"] == "Approved"
        assert steps[2]["completed"] is False
        assert steps[2]["detail"] == "Not yet issued"

    def test_a_rejected_request_shows_that_explicitly(self, client, db_session_factory) -> None:
        request_id = _seed_sync(db_session_factory, status=PurchaseRequestStatus.rejected)
        steps = client.get(f"/api/v1/procurement/requests/{request_id}/timeline").json()["steps"]
        assert steps[1]["detail"] == "Rejected"

    def test_an_issued_order_adds_two_more_steps(self, client, db_session_factory) -> None:
        request_id = _seed_sync(db_session_factory, status=PurchaseRequestStatus.approved)

        async def _add_order():
            async with db_session_factory() as session:
                session.add(PurchaseOrder(
                    id=uuid.uuid4(), company_id=COMPANY_ID, purchase_request_id=request_id,
                    vendor_name="Karachi Steel Co.", order_date=__import__("datetime").date.today(),
                    amount_pkr=980_000, expected_delivery=__import__("datetime").date.today(),
                    status=PurchaseOrderStatus.in_transit,
                ))
                await session.commit()
        import asyncio
        asyncio.get_event_loop().run_until_complete(_add_order())

        steps = client.get(f"/api/v1/procurement/requests/{request_id}/timeline").json()["steps"]
        assert len(steps) == 4
        assert steps[2]["completed"] is True
        assert steps[2]["detail"] == "Karachi Steel Co."
        assert steps[3]["completed"] is False
        assert steps[3]["detail"] == "Not yet delivered"

    def test_timeline_for_a_missing_request_is_a_404(self, client) -> None:
        assert client.get(f"/api/v1/procurement/requests/{uuid.uuid4()}/timeline").status_code == 404
