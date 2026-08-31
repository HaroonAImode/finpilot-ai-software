"""Invoice CRUD — list/filter, view, correct after review, and the two
lifecycle transitions (validate, send-to-accounting)."""
import io
import uuid
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.tenancy import get_company_id
from app.db.session import get_db
from app.main import app
from app.models import Invoice, InvoiceItem, InvoiceStatus, InvoiceType
from app.models.base import Base
from app.services.ai_engine_client import AIEngineError
from app.services.transactions_service_client import TransactionsServiceError

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


async def _seed_invoice(db_session_factory, *, company_id=COMPANY_ID, status=InvoiceStatus.needs_review, **overrides) -> uuid.UUID:
    async with db_session_factory() as db:
        invoice = Invoice(
            company_id=company_id, type=InvoiceType.purchase, status=status,
            vendor_name="ABC Traders", invoice_number="INV-1", total=1000.0,
            document_confidence=0.8, extraction_source="pdf_text", review_flags=[],
            raw_extraction_json={}, filename="invoice.pdf", size=100, s3_key="k/invoice.pdf",
            file_hash=uuid.uuid4().hex,
        )
        for key, value in overrides.items():
            setattr(invoice, key, value)
        invoice.items = [
            InvoiceItem(position=0, description="Item 1", qty=1.0, rate=1000.0, amount=1000.0, arithmetic_check="pass", review_flags=[]),
        ]
        db.add(invoice)
        await db.commit()
        await db.refresh(invoice)
        return invoice.id


def _seed(db_session_factory, **kwargs) -> uuid.UUID:
    import asyncio

    return asyncio.get_event_loop().run_until_complete(_seed_invoice(db_session_factory, **kwargs))


class TestListInvoices:
    def test_lists_invoices_for_the_callers_company_only(self, client, db_session_factory) -> None:
        _seed(db_session_factory)
        _seed(db_session_factory, company_id=OTHER_COMPANY_ID)

        response = client.get("/api/v1/invoices/")

        assert response.status_code == 200
        assert response.json()["total"] == 1

    def test_filters_by_status(self, client, db_session_factory) -> None:
        _seed(db_session_factory, status=InvoiceStatus.needs_review)
        _seed(db_session_factory, status=InvoiceStatus.processed)

        response = client.get("/api/v1/invoices/", params={"status": "processed"})

        assert response.json()["total"] == 1
        assert response.json()["invoices"][0]["status"] == "processed"

    def test_filters_by_date_range(self, client, db_session_factory) -> None:
        _seed(db_session_factory, invoice_date=date(2026, 6, 15))
        _seed(db_session_factory, invoice_date=date(2026, 7, 1))
        _seed(db_session_factory, invoice_date=None)  # undated — excluded once a bound is given

        response = client.get(
            "/api/v1/invoices/", params={"date_from": "2026-06-01", "date_to": "2026-06-30"},
        )

        assert response.json()["total"] == 1
        assert response.json()["invoices"][0]["invoice_date"] == "2026-06-15"

    def test_an_unknown_status_filter_400s(self, client) -> None:
        response = client.get("/api/v1/invoices/", params={"status": "not-a-real-status"})
        assert response.status_code == 400

    def test_pagination_fields_are_returned(self, client, db_session_factory) -> None:
        for _ in range(3):
            _seed(db_session_factory)

        response = client.get("/api/v1/invoices/", params={"skip": 1, "limit": 1})

        body = response.json()
        assert body["total"] == 3
        assert len(body["invoices"]) == 1
        assert body["skip"] == 1


class TestStatusSummary:
    def test_counts_are_grouped_by_status(self, client, db_session_factory) -> None:
        _seed(db_session_factory, status=InvoiceStatus.processed)
        _seed(db_session_factory, status=InvoiceStatus.processed)
        _seed(db_session_factory, status=InvoiceStatus.needs_review)

        body = client.get("/api/v1/invoices/status-summary").json()

        assert body["counts"]["processed"] == 2
        assert body["counts"]["needs_review"] == 1
        assert body["counts"]["validated"] == 0
        assert body["total"] == 3

    def test_defaults_to_purchase_only(self, client, db_session_factory) -> None:
        _seed(db_session_factory, status=InvoiceStatus.processed, type=InvoiceType.sale)
        body = client.get("/api/v1/invoices/status-summary").json()
        assert body["total"] == 0

    def test_type_all_includes_both(self, client, db_session_factory) -> None:
        _seed(db_session_factory, status=InvoiceStatus.processed)
        _seed(db_session_factory, status=InvoiceStatus.processed, type=InvoiceType.sale)
        body = client.get("/api/v1/invoices/status-summary", params={"type": "all"}).json()
        assert body["total"] == 2

    def test_only_this_company_s_invoices_are_counted(self, client, db_session_factory) -> None:
        _seed(db_session_factory, status=InvoiceStatus.processed)
        _seed(db_session_factory, status=InvoiceStatus.processed, company_id=OTHER_COMPANY_ID)
        body = client.get("/api/v1/invoices/status-summary").json()
        assert body["total"] == 1

    def test_an_unknown_type_is_a_clear_400(self, client) -> None:
        assert client.get("/api/v1/invoices/status-summary", params={"type": "bogus"}).status_code == 400


class TestGetInvoice:
    def test_returns_full_detail_including_items(self, client, db_session_factory) -> None:
        invoice_id = _seed(db_session_factory)

        response = client.get(f"/api/v1/invoices/{invoice_id}")

        assert response.status_code == 200
        body = response.json()
        assert body["vendor_name"] == "ABC Traders"
        assert len(body["items"]) == 1
        assert body["items"][0]["description"] == "Item 1"

    def test_another_companys_invoice_404s(self, client, db_session_factory) -> None:
        invoice_id = _seed(db_session_factory, company_id=OTHER_COMPANY_ID)
        response = client.get(f"/api/v1/invoices/{invoice_id}")
        assert response.status_code == 404


class TestGetInvoicePage:
    def _mock_storage(self) -> MagicMock:
        mock = MagicMock()
        mock.s3_client.get_object.return_value = {"Body": io.BytesIO(b"%PDF-fake-bytes")}
        return mock

    def test_streams_a_rendered_page(self, client, db_session_factory) -> None:
        invoice_id = _seed(db_session_factory)
        with patch("app.api.routes.invoices.StorageManager", return_value=self._mock_storage()), \
             patch("app.api.routes.invoices.render_invoice_page", AsyncMock(return_value=b"\x89PNG\r\n\x1a\nfakepngbytes")):
            response = client.get(f"/api/v1/invoices/{invoice_id}/page/0")

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content == b"\x89PNG\r\n\x1a\nfakepngbytes"

    def test_another_companys_invoice_404s(self, client, db_session_factory) -> None:
        invoice_id = _seed(db_session_factory, company_id=OTHER_COMPANY_ID)
        response = client.get(f"/api/v1/invoices/{invoice_id}/page/0")
        assert response.status_code == 404

    def test_an_ai_engine_failure_is_a_clean_502(self, client, db_session_factory) -> None:
        invoice_id = _seed(db_session_factory)
        with patch("app.api.routes.invoices.StorageManager", return_value=self._mock_storage()), \
             patch("app.api.routes.invoices.render_invoice_page", AsyncMock(side_effect=AIEngineError("boom"))):
            response = client.get(f"/api/v1/invoices/{invoice_id}/page/0")

        assert response.status_code == 502


class TestUpdateInvoice:
    def test_a_corrected_field_is_saved(self, client, db_session_factory) -> None:
        invoice_id = _seed(db_session_factory)

        response = client.put(f"/api/v1/invoices/{invoice_id}", json={"vendor_name": "Corrected Vendor Name"})

        assert response.status_code == 200
        assert response.json()["vendor_name"] == "Corrected Vendor Name"

    def test_omitted_fields_are_left_alone(self, client, db_session_factory) -> None:
        invoice_id = _seed(db_session_factory)

        response = client.put(f"/api/v1/invoices/{invoice_id}", json={"total": 5000.0})

        assert response.json()["total"] == 5000.0
        assert response.json()["vendor_name"] == "ABC Traders"  # untouched

    def test_correcting_line_items_replaces_the_full_set(self, client, db_session_factory) -> None:
        invoice_id = _seed(db_session_factory)

        response = client.put(
            f"/api/v1/invoices/{invoice_id}",
            json={"items": [{"description": "Corrected item", "qty": 2, "rate": 500, "amount": 1000}]},
        )

        assert response.status_code == 200
        assert len(response.json()["items"]) == 1
        assert response.json()["items"][0]["description"] == "Corrected item"

    def test_a_correction_never_touches_the_raw_extraction(self, client, db_session_factory) -> None:
        import asyncio

        async def _seed_with_raw():
            async with db_session_factory() as db:
                invoice = Invoice(
                    company_id=COMPANY_ID, type=InvoiceType.purchase, status=InvoiceStatus.needs_review,
                    vendor_name="Original Name", document_confidence=0.5, extraction_source="pdf_text",
                    review_flags=[], raw_extraction_json={"vendor_name": {"value": "Original Name"}},
                    filename="invoice.pdf", size=100, s3_key="k/invoice.pdf", file_hash=uuid.uuid4().hex,
                )
                db.add(invoice)
                await db.commit()
                await db.refresh(invoice)
                return invoice.id

        invoice_id = asyncio.get_event_loop().run_until_complete(_seed_with_raw())

        client.put(f"/api/v1/invoices/{invoice_id}", json={"vendor_name": "Corrected Name"})

        async def _reload():
            async with db_session_factory() as db:
                return await db.get(Invoice, invoice_id)

        reloaded = asyncio.get_event_loop().run_until_complete(_reload())
        assert reloaded.vendor_name == "Corrected Name"
        assert reloaded.raw_extraction_json["vendor_name"]["value"] == "Original Name"


class TestValidateInvoice:
    def test_validating_sets_status_to_validated(self, client, db_session_factory) -> None:
        invoice_id = _seed(db_session_factory, status=InvoiceStatus.needs_review)

        response = client.post(f"/api/v1/invoices/{invoice_id}/validate")

        assert response.json()["status"] == "validated"


class TestSendToAccounting:
    def test_a_processed_invoice_can_be_sent(self, client, db_session_factory) -> None:
        invoice_id = _seed(db_session_factory, status=InvoiceStatus.processed)

        response = client.post(f"/api/v1/invoices/{invoice_id}/send-to-accounting")

        assert response.status_code == 200
        assert response.json()["status"] == "sent_to_accounting"

    def test_a_validated_invoice_can_be_sent(self, client, db_session_factory) -> None:
        invoice_id = _seed(db_session_factory, status=InvoiceStatus.validated)

        response = client.post(f"/api/v1/invoices/{invoice_id}/send-to-accounting")

        assert response.status_code == 200

    def test_a_needs_review_invoice_is_refused(self, client, db_session_factory) -> None:
        """Protects accounting data integrity — an unreviewed extraction
        must not be booked, per docs/invoice-ocr-plan.md §2a."""
        invoice_id = _seed(db_session_factory, status=InvoiceStatus.needs_review_high_priority)

        response = client.post(f"/api/v1/invoices/{invoice_id}/send-to-accounting")

        assert response.status_code == 409


class TestSendToAccountingBooksAnExpense:
    """The real hand-off to Transactions Service — see
    transactions_service_client.py. Patched throughout: this is about
    *this* service calling out correctly and not being blocked by a
    failure, not about Transactions Service actually being reachable."""

    def test_a_purchase_invoice_books_an_expense(self, client, db_session_factory) -> None:
        invoice_id = _seed(db_session_factory, status=InvoiceStatus.processed)
        with patch(
            "app.api.routes.invoices.book_expense_from_invoice", AsyncMock(return_value=None),
        ) as mock:
            response = client.post(f"/api/v1/invoices/{invoice_id}/send-to-accounting")

        assert response.status_code == 200
        mock.assert_awaited_once()
        assert mock.await_args.kwargs["invoice_id"] == invoice_id
        assert mock.await_args.kwargs["amount"] == 1000.0

    def test_a_sale_invoice_does_not_book_an_expense(self, client, db_session_factory) -> None:
        """Revenue stays Invoice Service's own scanned-sales ledger — no
        second call for a sale, see the Transactions Service design spec's
        scope note."""
        invoice_id = _seed(db_session_factory, status=InvoiceStatus.processed, type=InvoiceType.sale)
        with patch(
            "app.api.routes.invoices.book_expense_from_invoice", AsyncMock(return_value=None),
        ) as mock:
            response = client.post(f"/api/v1/invoices/{invoice_id}/send-to-accounting")

        assert response.status_code == 200
        mock.assert_not_awaited()

    def test_transactions_service_being_unreachable_does_not_block_the_status_flip(
        self, client, db_session_factory,
    ) -> None:
        """The whole point of the best-effort call — an accountant clicking
        Send to Accounting should not be stuck on a downstream hiccup."""
        invoice_id = _seed(db_session_factory, status=InvoiceStatus.processed)
        with patch(
            "app.api.routes.invoices.book_expense_from_invoice",
            AsyncMock(side_effect=TransactionsServiceError("down")),
        ):
            response = client.post(f"/api/v1/invoices/{invoice_id}/send-to-accounting")

        assert response.status_code == 200
        assert response.json()["status"] == "sent_to_accounting"


class TestStatusEditingFromTheRecordsGrid:
    """The Saved Records grid can edit status inline, so the PUT endpoint had
    to accept it. These guard the boundary that makes that safe: an admin may
    move a record *between* the two saved states, but the grid must not become
    a way to skip the extraction lifecycle entirely."""

    def test_a_validated_invoice_can_be_moved_to_sent_to_accounting(self, client, db_session_factory) -> None:
        invoice_id = _seed(db_session_factory, status=InvoiceStatus.validated)
        response = client.put(f"/api/v1/invoices/{invoice_id}", json={"status": "sent_to_accounting"})
        assert response.status_code == 200
        assert response.json()["status"] == "sent_to_accounting"

    def test_it_can_be_moved_back(self, client, db_session_factory) -> None:
        invoice_id = _seed(db_session_factory, status=InvoiceStatus.sent_to_accounting)
        response = client.put(f"/api/v1/invoices/{invoice_id}", json={"status": "validated"})
        assert response.status_code == 200
        assert response.json()["status"] == "validated"

    def test_an_unreviewed_invoice_cannot_be_promoted_through_the_grid(self, client, db_session_factory) -> None:
        """The same guard POST /send-to-accounting enforces — otherwise the
        grid's PUT would be a hole straight past it."""
        invoice_id = _seed(db_session_factory, status=InvoiceStatus.needs_review)
        response = client.put(f"/api/v1/invoices/{invoice_id}", json={"status": "sent_to_accounting"})
        assert response.status_code == 409
        assert "has not been validated" in response.json()["detail"]

    def test_a_status_outside_the_two_saved_states_is_rejected(self, client, db_session_factory) -> None:
        """needs_review is not something a human types into a grid — it is a
        conclusion the rules engine reached."""
        invoice_id = _seed(db_session_factory, status=InvoiceStatus.validated)
        assert client.put(f"/api/v1/invoices/{invoice_id}", json={"status": "needs_review"}).status_code == 422

    def test_editing_other_fields_still_works_on_an_unreviewed_invoice(self, client, db_session_factory) -> None:
        """The status guard must not block ordinary corrections — those are
        exactly what an invoice in needs_review is waiting for."""
        invoice_id = _seed(db_session_factory, status=InvoiceStatus.needs_review)
        response = client.put(f"/api/v1/invoices/{invoice_id}", json={"vendor_name": "Corrected Ltd"})
        assert response.status_code == 200
        assert response.json()["vendor_name"] == "Corrected Ltd"


class TestVendorLinkAndSpend:
    """`vendor_id` links an invoice to a Vendors Service record, and
    /vendor-spend aggregates by it so that service can derive
    total_spend_pkr in one cross-service call rather than one per vendor."""

    def test_invoices_can_be_filtered_by_vendor(self, client, db_session_factory) -> None:
        vendor_a, vendor_b = uuid.uuid4(), uuid.uuid4()
        _seed(db_session_factory, status=InvoiceStatus.validated, vendor_id=vendor_a)
        _seed(db_session_factory, status=InvoiceStatus.validated, vendor_id=vendor_a)
        _seed(db_session_factory, status=InvoiceStatus.validated, vendor_id=vendor_b)

        assert client.get(f"/api/v1/invoices/?vendor_id={vendor_a}").json()["total"] == 2
        assert client.get(f"/api/v1/invoices/?vendor_id={vendor_b}").json()["total"] == 1

    def test_spend_is_aggregated_per_vendor(self, client, db_session_factory) -> None:
        vendor = uuid.uuid4()
        _seed(db_session_factory, status=InvoiceStatus.validated, vendor_id=vendor, total=1000.0)
        _seed(db_session_factory, status=InvoiceStatus.validated, vendor_id=vendor, total=250.0)

        rows = client.get("/api/v1/invoices/vendor-spend").json()
        row = next(r for r in rows if r["vendor_id"] == str(vendor))
        assert row["total_spend"] == 1250.0
        assert row["invoice_count"] == 2

    def test_unlinked_invoices_are_not_counted(self, client, db_session_factory) -> None:
        """An invoice with no vendor_id has no vendor to attribute spend to —
        counting it would inflate someone's total with another's money."""
        _seed(db_session_factory, status=InvoiceStatus.validated, vendor_id=None, total=9999.0)
        assert client.get("/api/v1/invoices/vendor-spend").json() == []

    def test_sales_invoices_are_excluded_from_spend(self, client, db_session_factory) -> None:
        """Money billed *out* is not spend."""
        vendor = uuid.uuid4()
        _seed(db_session_factory, status=InvoiceStatus.validated, vendor_id=vendor,
              total=500.0, type=InvoiceType.sale)
        assert client.get("/api/v1/invoices/vendor-spend").json() == []

    def test_another_company_s_spend_is_not_included(self, client, db_session_factory) -> None:
        vendor = uuid.uuid4()
        _seed(db_session_factory, company_id=OTHER_COMPANY_ID, status=InvoiceStatus.validated,
              vendor_id=vendor, total=777.0)
        assert client.get("/api/v1/invoices/vendor-spend").json() == []

    def test_spend_can_be_scoped_to_a_date_range(self, client, db_session_factory) -> None:
        """Added for Reports Service's Purchase Report — see
        invoice_date's own docstring on the endpoint."""
        vendor = uuid.uuid4()
        _seed(db_session_factory, status=InvoiceStatus.validated, vendor_id=vendor,
              total=1000.0, invoice_date=date(2026, 6, 15))
        _seed(db_session_factory, status=InvoiceStatus.validated, vendor_id=vendor,
              total=500.0, invoice_date=date(2026, 7, 15))

        rows = client.get(
            f"/api/v1/invoices/vendor-spend?date_from=2026-07-01&date_to=2026-07-31"
        ).json()
        row = next(r for r in rows if r["vendor_id"] == str(vendor))
        assert row["total_spend"] == 500.0

    def test_a_row_with_no_invoice_date_is_excluded_once_a_range_is_given(self, client, db_session_factory) -> None:
        vendor = uuid.uuid4()
        _seed(db_session_factory, status=InvoiceStatus.validated, vendor_id=vendor,
              total=1000.0, invoice_date=None)
        rows = client.get("/api/v1/invoices/vendor-spend?date_from=2026-07-01&date_to=2026-07-31").json()
        assert rows == []

    def test_vendor_spend_is_not_parsed_as_an_invoice_id(self, client) -> None:
        """The literal path must be matched before /invoices/{invoice_id},
        which would otherwise try to read "vendor-spend" as a UUID."""
        assert client.get("/api/v1/invoices/vendor-spend").status_code == 200

    def test_an_invoice_can_be_linked_to_a_vendor(self, client, db_session_factory) -> None:
        vendor = uuid.uuid4()
        invoice_id = _seed(db_session_factory, status=InvoiceStatus.validated)
        response = client.put(f"/api/v1/invoices/{invoice_id}", json={"vendor_id": str(vendor)})
        assert response.status_code == 200
        assert response.json()["vendor_id"] == str(vendor)


class TestVendorReconciliationQueue:
    """The reconciliation queue's backend: group unlinked invoices by their
    raw vendor_name, and let one action link the whole group at once. Real
    data drove the grouping decision — see docs/documents-and-vendors-plan.md
    §3.8: several invoices routinely share one bad OCR string verbatim."""

    def test_invoices_sharing_a_name_are_grouped_together(self, client, db_session_factory) -> None:
        _seed(db_session_factory, status=InvoiceStatus.validated, vendor_name="Karachi Steel", total=1000.0)
        _seed(db_session_factory, status=InvoiceStatus.validated, vendor_name="Karachi Steel", total=500.0)
        _seed(db_session_factory, status=InvoiceStatus.validated, vendor_name="Other Supplier", total=200.0)

        groups = client.get("/api/v1/invoices/vendor-groups").json()

        karachi = next(g for g in groups if g["vendor_name"] == "Karachi Steel")
        assert karachi["invoice_count"] == 2
        assert karachi["total_amount"] == 1500.0
        assert len(karachi["invoice_ids"]) == 2

    def test_already_linked_invoices_are_excluded(self, client, db_session_factory) -> None:
        _seed(db_session_factory, status=InvoiceStatus.validated, vendor_name="Linked Co", vendor_id=uuid.uuid4())
        names = [g["vendor_name"] for g in client.get("/api/v1/invoices/vendor-groups").json()]
        assert "Linked Co" not in names

    def test_blank_vendor_name_is_excluded(self, client, db_session_factory) -> None:
        """Nothing to review — there is no string, and linking these needs a
        human to read the source document, not a matching queue."""
        _seed(db_session_factory, status=InvoiceStatus.validated, vendor_name=None)
        _seed(db_session_factory, status=InvoiceStatus.validated, vendor_name="   ")
        assert client.get("/api/v1/invoices/vendor-groups").json() == []

    def test_sales_invoices_are_excluded(self, client, db_session_factory) -> None:
        """A customer name is not a vendor to reconcile."""
        _seed(db_session_factory, status=InvoiceStatus.validated, vendor_name="Some Customer", type=InvoiceType.sale)
        assert client.get("/api/v1/invoices/vendor-groups").json() == []

    def test_another_company_s_invoices_are_not_included(self, client, db_session_factory) -> None:
        _seed(db_session_factory, company_id=OTHER_COMPANY_ID, status=InvoiceStatus.validated, vendor_name="Theirs")
        assert client.get("/api/v1/invoices/vendor-groups").json() == []

    def test_vendor_groups_is_not_parsed_as_an_invoice_id(self, client) -> None:
        assert client.get("/api/v1/invoices/vendor-groups").status_code == 200

    def test_bulk_link_sets_vendor_id_on_every_invoice_in_the_group(self, client, db_session_factory) -> None:
        vendor = uuid.uuid4()
        id_a = _seed(db_session_factory, status=InvoiceStatus.validated, vendor_name="Karachi Steel")
        id_b = _seed(db_session_factory, status=InvoiceStatus.validated, vendor_name="Karachi Steel")

        response = client.post(
            "/api/v1/invoices/bulk-link-vendor",
            json={"invoice_ids": [str(id_a), str(id_b)], "vendor_id": str(vendor)},
        )
        assert response.status_code == 200
        assert response.json()["updated_count"] == 2
        assert client.get(f"/api/v1/invoices/{id_a}").json()["vendor_id"] == str(vendor)
        assert client.get(f"/api/v1/invoices/{id_b}").json()["vendor_id"] == str(vendor)

    def test_bulk_link_ignores_ids_from_another_company(self, client, db_session_factory) -> None:
        """The batch is scoped from this company's own vendor-groups
        response; an id that no longer matches is silently skipped rather
        than failing the rest of a real accountant's batch."""
        vendor = uuid.uuid4()
        theirs = _seed(db_session_factory, company_id=OTHER_COMPANY_ID, status=InvoiceStatus.validated)

        response = client.post(
            "/api/v1/invoices/bulk-link-vendor",
            json={"invoice_ids": [str(theirs)], "vendor_id": str(vendor)},
        )
        assert response.status_code == 200
        assert response.json()["updated_count"] == 0

    def test_bulk_link_requires_at_least_one_invoice(self, client) -> None:
        response = client.post(
            "/api/v1/invoices/bulk-link-vendor", json={"invoice_ids": [], "vendor_id": str(uuid.uuid4())},
        )
        assert response.status_code == 422
