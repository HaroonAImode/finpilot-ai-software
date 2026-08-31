"""Non-transactional document handling — the API half.

The concept these guard: "is this a financial transaction document at all?"
is a *separate axis* from "where is this in the human review workflow".
Internal paperwork (a minute sheet, an approval request) carries amounts but
is not a purchase, and must not be force-fitted into a cashbook category or
have its stated amount treated as a transaction total.
"""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.tenancy import get_company_id
from app.db.session import get_db
from app.main import app
from app.models import (
    ClassificationSource, Invoice, InvoiceItem, InvoiceStatus, InvoiceType, TransactionStatus,
)
from app.models.base import Base

COMPANY_ID = uuid.uuid4()


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


async def _seed_invoice(db_session_factory, **overrides) -> uuid.UUID:
    async with db_session_factory() as db:
        invoice = Invoice(
            company_id=COMPANY_ID, type=InvoiceType.purchase, status=InvoiceStatus.needs_review,
            vendor_name="STIXOR", total=None, document_confidence=0.8, extraction_source="ocr",
            review_flags=[], raw_extraction_json={"document_type": {"value": "minute_sheet"}},
            filename="memo.jpg", size=100, s3_key="k/memo.jpg", file_hash=uuid.uuid4().hex,
        )
        for key, value in overrides.items():
            setattr(invoice, key, value)
        invoice.items = [InvoiceItem(position=0, description="x", arithmetic_check="not_checked", review_flags=[])]
        db.add(invoice)
        await db.commit()
        await db.refresh(invoice)
        return invoice.id


def _seed(db_session_factory, **kwargs) -> uuid.UUID:
    import asyncio

    return asyncio.get_event_loop().run_until_complete(_seed_invoice(db_session_factory, **kwargs))


class TestNonTransactionalListing:
    def test_the_non_transactional_area_lists_only_those_documents(self, client, db_session_factory) -> None:
        """What the dedicated Non-Transactional Documents area queries —
        these documents must not be mixed into the normal cashbook list."""
        _seed(db_session_factory, transaction_status=TransactionStatus.non_transactional, amount_mentioned=22875.0)
        _seed(db_session_factory, transaction_status=TransactionStatus.transactional, total=1000.0)

        response = client.get("/api/v1/invoices/", params={"transaction_status": "non_transactional"})

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["invoices"][0]["transaction_status"] == "non_transactional"
        assert body["invoices"][0]["amount_mentioned"] == 22875.0
        # The critical distinction, visible right in the list payload.
        assert body["invoices"][0]["total"] is None

    def test_transactional_documents_are_unaffected_by_the_new_filter(self, client, db_session_factory) -> None:
        _seed(db_session_factory, transaction_status=TransactionStatus.transactional, total=1000.0)

        response = client.get("/api/v1/invoices/", params={"transaction_status": "transactional"})

        assert response.json()["total"] == 1
        assert response.json()["invoices"][0]["total"] == 1000.0

    def test_omitting_the_filter_still_returns_everything(self, client, db_session_factory) -> None:
        """Pre-existing callers pass no transaction_status at all and must
        keep seeing exactly what they saw before."""
        _seed(db_session_factory, transaction_status=TransactionStatus.non_transactional)
        _seed(db_session_factory, transaction_status=TransactionStatus.transactional)

        assert client.get("/api/v1/invoices/").json()["total"] == 2

    def test_an_unknown_transaction_status_is_rejected(self, client, db_session_factory) -> None:
        assert client.get("/api/v1/invoices/", params={"transaction_status": "nonsense"}).status_code == 400


class TestReject:
    def test_reject_is_a_soft_state_that_preserves_the_document(self, client, db_session_factory) -> None:
        """Rejecting must not delete the row: the source file, extraction
        and audit trail all stay, and the decision stays reversible."""
        invoice_id = _seed(db_session_factory, transaction_status=TransactionStatus.non_transactional)

        response = client.post(f"/api/v1/invoices/{invoice_id}/reject")

        assert response.status_code == 200
        assert response.json()["status"] == "rejected"
        # Still retrievable, with its source file reference and audit blob intact.
        fetched = client.get(f"/api/v1/invoices/{invoice_id}").json()
        assert fetched["status"] == "rejected"
        assert fetched["filename"] == "memo.jpg"


class TestReclassifyUserOverride:
    def test_process_as_transaction_promotes_amount_mentioned_to_total(self, client, db_session_factory) -> None:
        """The "Process as Transaction" action: the figure the reviewer was
        already looking at as "Amount mentioned" becomes the total — not an
        invented number, the one the document stated."""
        invoice_id = _seed(
            db_session_factory,
            transaction_status=TransactionStatus.non_transactional, amount_mentioned=22875.0, total=None,
        )

        response = client.post(
            f"/api/v1/invoices/{invoice_id}/reclassify", json={"transaction_status": "transactional"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["transaction_status"] == "transactional"
        assert body["total"] == 22875.0
        assert body["amount_mentioned"] is None
        # The disagreement is recorded, not silent.
        assert body["classification_source"] == "user_override"

    def test_the_original_rule_classification_is_never_overwritten(self, client, db_session_factory) -> None:
        """Rule 8.1 applied to classification: a human correction records
        that a person disagreed; it does not erase what the engine said."""
        invoice_id = _seed(
            db_session_factory,
            transaction_status=TransactionStatus.non_transactional, amount_mentioned=22875.0,
        )

        client.post(f"/api/v1/invoices/{invoice_id}/reclassify", json={"transaction_status": "transactional"})

        fetched = client.get(f"/api/v1/invoices/{invoice_id}").json()
        assert fetched["classification_source"] == "user_override"
        # The engine's own verdict is still readable in the extraction record.
        assert fetched["extracted_fields"].get("document_type", {}).get("value") == "minute_sheet"

    def test_demoting_to_non_transactional_moves_the_total_back_out(self, client, db_session_factory) -> None:
        """The reverse correction must not leave a transaction total behind
        on a document that is no longer a transaction."""
        invoice_id = _seed(
            db_session_factory,
            transaction_status=TransactionStatus.transactional, total=5000.0, category="Office Entertainment",
        )

        response = client.post(
            f"/api/v1/invoices/{invoice_id}/reclassify", json={"transaction_status": "non_transactional"},
        )

        body = response.json()
        assert body["transaction_status"] == "non_transactional"
        assert body["total"] is None
        assert body["amount_mentioned"] == 5000.0
        # A cashbook category is meaningless on a non-purchase.
        assert body["category"] is None

    def test_a_promoted_document_with_no_stated_amount_gets_no_invented_total(self, client, db_session_factory) -> None:
        invoice_id = _seed(
            db_session_factory,
            transaction_status=TransactionStatus.non_transactional, amount_mentioned=None, total=None,
        )

        body = client.post(
            f"/api/v1/invoices/{invoice_id}/reclassify", json={"transaction_status": "transactional"},
        ).json()

        assert body["transaction_status"] == "transactional"
        assert body["total"] is None

    def test_confirm_reuses_the_existing_validate_endpoint(self, client, db_session_factory) -> None:
        """"Confirm" ("yes, this really is a minute sheet") is the same act
        as validating any other document — a person reviewed the system's
        output and agreed — so it needs no second endpoint, and must leave
        the classification alone."""
        invoice_id = _seed(
            db_session_factory,
            transaction_status=TransactionStatus.non_transactional, amount_mentioned=22875.0,
        )

        body = client.post(f"/api/v1/invoices/{invoice_id}/validate").json()

        assert body["status"] == "validated"
        assert body["transaction_status"] == "non_transactional"
        assert body["classification_source"] == "rule"
        assert body["amount_mentioned"] == 22875.0
