"""Revenue Manager — scan-to-revenue (docs/superpowers/specs/2026-08-27-
revenue-manager-scan-design.md). Mirrors test_scanner_route.py's own
split: AI Engine and S3 storage are mocked here; a real end-to-end run
against the running Docker stack is the live-verification half.
"""
import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from unittest.mock import AsyncMock, patch

from app.core.tenancy import get_company_id
from app.db.session import get_db
from app.main import app
from app.models import Invoice, InvoiceStatus, InvoiceType
from app.models.base import Base
from app.schemas.ai_extraction import (
    ArithmeticValidationSchema, ExtractedFieldSchema, ExtractedInvoiceSchema,
)
from app.services.ai_engine_client import AIEngineError

COMPANY_ID = uuid.uuid4()


def _field(value=None, confidence=1.0, method="label_anchor+pattern") -> ExtractedFieldSchema:
    return ExtractedFieldSchema(value=value, confidence=confidence, method=method)


def _extraction(**overrides) -> ExtractedInvoiceSchema:
    defaults = dict(
        vendor_name=_field("Al-Madina Retail"), customer_name=_field(None),
        invoice_number=_field("INV-2026-0184"), invoice_date=_field("2026-08-04"),
        ntn=_field(None), subtotal=_field(None), tax_rate=_field(None), tax_amount=_field(None),
        total=_field(45396.0), line_items=[],
        arithmetic_validation=ArithmeticValidationSchema(
            line_items_pass_rate=None, totals_pass=None, tax_rate_plausible=None, date_plausible=True,
        ),
        document_confidence=0.9, extraction_source="pdf_text", review_status="auto_processed", review_flags=[],
    )
    defaults.update(overrides)
    return ExtractedInvoiceSchema(**defaults)


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


def _mock_storage():
    mock = AsyncMock()
    mock.store_bytes = AsyncMock(return_value=("job-key/receipt.jpg", "hash"))
    return mock


async def _seed_scanned_sale(db_session_factory, **overrides) -> uuid.UUID:
    defaults = dict(
        company_id=COMPANY_ID, type=InvoiceType.sale, status=InvoiceStatus.processed,
        customer_name="Al-Madina Retail", total=1000.0, invoice_date=date(2026, 8, 1),
        document_confidence=0.9, extraction_source="pdf_text", review_flags=[],
        raw_extraction_json={}, filename="receipt.jpg", size=100, s3_key="k/receipt.jpg",
        file_hash=uuid.uuid4().hex,
    )
    defaults.update(overrides)
    async with db_session_factory() as db:
        invoice = Invoice(**defaults)
        db.add(invoice)
        await db.commit()
        await db.refresh(invoice)
        return invoice.id


def _seed_sync(db_session_factory, **kwargs) -> uuid.UUID:
    import asyncio

    return asyncio.get_event_loop().run_until_complete(_seed_scanned_sale(db_session_factory, **kwargs))


class TestScanSuccess:
    def test_a_successful_scan_creates_a_sale_invoice(self, client) -> None:
        with patch(
            "app.api.routes.sales_scanner.extract_invoice_data", AsyncMock(return_value=_extraction()),
        ), patch("app.api.routes.sales_scanner.StorageManager", return_value=_mock_storage()):
            response = client.post(
                "/api/v1/invoices/sales/scan",
                files={"file": ("receipt.jpg", b"fake-image-bytes", "image/jpeg")},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "done"
        assert body["invoice"]["type"] == "sale"
        assert body["invoice"]["total"] == 45396.0
        assert body["invoice"]["status"] == "processed"

    def test_customer_name_falls_back_to_vendor_name(self, client) -> None:
        """The rules engine reports a sale's counterparty under
        vendor_name regardless — see invoice_builder.py's own docstring."""
        with patch(
            "app.api.routes.sales_scanner.extract_invoice_data",
            AsyncMock(return_value=_extraction(vendor_name=_field("Al-Madina Retail"), customer_name=_field(None))),
        ), patch("app.api.routes.sales_scanner.StorageManager", return_value=_mock_storage()):
            response = client.post(
                "/api/v1/invoices/sales/scan",
                files={"file": ("receipt.jpg", b"bytes", "image/jpeg")},
            )

        assert response.json()["invoice"]["customer_name"] == "Al-Madina Retail"

    def test_no_known_vendors_is_passed_to_extraction(self, client) -> None:
        """Rule 3.4/7.3's fuzzy match is a purchase-side concept (matches
        against this company's vendor directory) — a sale's counterparty
        is a customer, and there is no customer directory to match
        against in v1 (design spec §4)."""
        extract_mock = AsyncMock(return_value=_extraction())
        with patch("app.api.routes.sales_scanner.extract_invoice_data", extract_mock), \
             patch("app.api.routes.sales_scanner.StorageManager", return_value=_mock_storage()):
            client.post("/api/v1/invoices/sales/scan", files={"file": ("receipt.jpg", b"bytes", "image/jpeg")})

        assert extract_mock.call_args.kwargs.get("known_vendors") is None

    def test_scan_job_can_be_polled_afterward(self, client) -> None:
        with patch(
            "app.api.routes.sales_scanner.extract_invoice_data", AsyncMock(return_value=_extraction()),
        ), patch("app.api.routes.sales_scanner.StorageManager", return_value=_mock_storage()):
            scan_response = client.post(
                "/api/v1/invoices/sales/scan", files={"file": ("receipt.jpg", b"bytes", "image/jpeg")},
            )
        job_id = scan_response.json()["job_id"]

        poll_response = client.get(f"/api/v1/invoices/sales/scan/{job_id}")

        assert poll_response.status_code == 200
        assert poll_response.json()["status"] == "done"
        assert poll_response.json()["invoice"]["type"] == "sale"

    def test_scan_is_not_parsed_as_an_invoice_id_by_the_generator_router(self, client) -> None:
        """The literal path must match before sales.py's own
        /invoices/sales/{invoice_id}, which would otherwise try to read
        "scan" as a UUID."""
        with patch(
            "app.api.routes.sales_scanner.extract_invoice_data", AsyncMock(return_value=_extraction()),
        ), patch("app.api.routes.sales_scanner.StorageManager", return_value=_mock_storage()):
            response = client.post(
                "/api/v1/invoices/sales/scan", files={"file": ("receipt.jpg", b"bytes", "image/jpeg")},
            )
        assert response.status_code == 200


class TestScanDeduplication:
    def test_scanning_the_same_file_twice_returns_the_existing_invoice(self, client) -> None:
        extract_mock = AsyncMock(return_value=_extraction())
        with patch("app.api.routes.sales_scanner.extract_invoice_data", extract_mock), \
             patch("app.api.routes.sales_scanner.StorageManager", return_value=_mock_storage()):
            first = client.post(
                "/api/v1/invoices/sales/scan", files={"file": ("receipt.jpg", b"identical-bytes", "image/jpeg")},
            )
            second = client.post(
                "/api/v1/invoices/sales/scan", files={"file": ("receipt.jpg", b"identical-bytes", "image/jpeg")},
            )

        assert first.json()["invoice_id"] == second.json()["invoice_id"]
        assert extract_mock.call_count == 1

    def test_a_purchase_invoice_with_the_same_hash_is_not_treated_as_a_duplicate(
        self, client, db_session_factory,
    ) -> None:
        """The dedup lookup is scoped to type=sale — a coincidental hash
        match against a purchase invoice must never be handed back as if
        it were a scanned sale."""
        _seed_sync(db_session_factory, type=InvoiceType.purchase, file_hash="shared-hash", extraction_source="pdf_text")

        extract_mock = AsyncMock(return_value=_extraction())
        with patch("app.api.routes.sales_scanner.extract_invoice_data", extract_mock), \
             patch("app.api.routes.sales_scanner.StorageManager", return_value=_mock_storage()):
            # sha256 of these exact bytes must equal "shared-hash" for the
            # test to mean anything — simplest is to patch hashlib instead
            # of hand-crafting bytes with that literal digest.
            with patch("app.api.routes.sales_scanner.hashlib.sha256") as mock_sha:
                mock_sha.return_value.hexdigest.return_value = "shared-hash"
                response = client.post(
                    "/api/v1/invoices/sales/scan", files={"file": ("receipt.jpg", b"any-bytes", "image/jpeg")},
                )

        assert extract_mock.call_count == 1
        assert response.json()["invoice"]["type"] == "sale"


class TestScanFailureHandling:
    def test_ai_engine_unreachable_is_a_clean_502_not_a_500(self, client) -> None:
        with patch(
            "app.api.routes.sales_scanner.extract_invoice_data",
            AsyncMock(side_effect=AIEngineError("Could not reach AI Engine")),
        ):
            response = client.post(
                "/api/v1/invoices/sales/scan", files={"file": ("receipt.jpg", b"bytes", "image/jpeg")},
            )
        assert response.status_code == 502

    def test_storage_failure_is_a_clean_502(self, client) -> None:
        with patch(
            "app.api.routes.sales_scanner.extract_invoice_data", AsyncMock(return_value=_extraction()),
        ):
            broken_storage = AsyncMock()
            broken_storage.store_bytes = AsyncMock(side_effect=RuntimeError("disk full"))
            with patch("app.api.routes.sales_scanner.StorageManager", return_value=broken_storage):
                response = client.post(
                    "/api/v1/invoices/sales/scan", files={"file": ("receipt.jpg", b"bytes", "image/jpeg")},
                )
        assert response.status_code == 502


class TestListScannedSales:
    def test_scanned_sales_are_listed(self, client, db_session_factory) -> None:
        _seed_sync(db_session_factory, extraction_source="pdf_text")
        _seed_sync(db_session_factory, extraction_source="ocr")

        response = client.get("/api/v1/invoices/sales/scanned")

        assert response.status_code == 200
        assert response.json()["total"] == 2

    def test_authored_sales_invoices_are_excluded(self, client, db_session_factory) -> None:
        """A sales invoice typed through the Invoice Generator has
        extraction_source="generated" — it belongs to that surface, not
        the Revenue Manager's scanned list (design spec §5)."""
        _seed_sync(db_session_factory, extraction_source="pdf_text")
        _seed_sync(db_session_factory, extraction_source="generated")

        assert client.get("/api/v1/invoices/sales/scanned").json()["total"] == 1

    def test_purchase_invoices_are_excluded(self, client, db_session_factory) -> None:
        _seed_sync(db_session_factory, type=InvoiceType.purchase, extraction_source="pdf_text", customer_name=None)
        assert client.get("/api/v1/invoices/sales/scanned").json()["total"] == 0

    def test_another_company_s_scanned_sales_are_not_included(self, client, db_session_factory) -> None:
        _seed_sync(db_session_factory, company_id=uuid.uuid4(), extraction_source="pdf_text")
        assert client.get("/api/v1/invoices/sales/scanned").json()["total"] == 0

    def test_status_filter_accepts_a_comma_separated_list(self, client, db_session_factory) -> None:
        _seed_sync(db_session_factory, status=InvoiceStatus.processed, extraction_source="pdf_text")
        _seed_sync(db_session_factory, status=InvoiceStatus.needs_review, extraction_source="pdf_text")
        _seed_sync(db_session_factory, status=InvoiceStatus.sent_to_accounting, extraction_source="pdf_text")

        response = client.get("/api/v1/invoices/sales/scanned?status=processed,needs_review")
        assert response.json()["total"] == 2

    def test_an_unknown_status_is_a_clean_400(self, client) -> None:
        assert client.get("/api/v1/invoices/sales/scanned?status=not-a-real-status").status_code == 400

    def test_scanned_is_not_parsed_as_an_invoice_id(self, client) -> None:
        assert client.get("/api/v1/invoices/sales/scanned").status_code == 200


class TestSalesSummary:
    def test_totals_sum_across_all_scanned_sales(self, client, db_session_factory) -> None:
        _seed_sync(db_session_factory, total=1000.0, extraction_source="pdf_text")
        _seed_sync(db_session_factory, total=500.0, extraction_source="ocr")

        response = client.get("/api/v1/invoices/sales/summary")

        assert response.status_code == 200
        body = response.json()
        assert body["totals"]["revenue"] == 1500.0
        assert body["totals"]["document_count"] == 2

    def test_a_missing_total_counts_as_zero_not_a_crash(self, client, db_session_factory) -> None:
        _seed_sync(db_session_factory, total=None, extraction_source="pdf_text")
        response = client.get("/api/v1/invoices/sales/summary")
        assert response.json()["totals"]["revenue"] == 0.0

    def test_pending_review_counts_needs_review_statuses_only(self, client, db_session_factory) -> None:
        _seed_sync(db_session_factory, status=InvoiceStatus.processed, extraction_source="pdf_text")
        _seed_sync(db_session_factory, status=InvoiceStatus.needs_review, extraction_source="pdf_text")
        _seed_sync(db_session_factory, status=InvoiceStatus.needs_review_high_priority, extraction_source="pdf_text")
        _seed_sync(db_session_factory, status=InvoiceStatus.sent_to_accounting, extraction_source="pdf_text")

        assert client.get("/api/v1/invoices/sales/summary").json()["totals"]["pending_review"] == 2

    def test_monthly_revenue_is_bucketed_by_invoice_date(self, client, db_session_factory) -> None:
        _seed_sync(db_session_factory, total=100.0, invoice_date=date(2026, 6, 15), extraction_source="pdf_text")
        _seed_sync(db_session_factory, total=200.0, invoice_date=date(2026, 6, 20), extraction_source="pdf_text")
        _seed_sync(db_session_factory, total=300.0, invoice_date=date(2026, 7, 1), extraction_source="pdf_text")

        monthly = client.get("/api/v1/invoices/sales/summary").json()["monthly"]

        assert monthly == [
            {"month": "2026-06", "total": 300.0, "count": 2},
            {"month": "2026-07", "total": 300.0, "count": 1},
        ]

    def test_top_customers_are_sorted_descending_and_capped_at_five(self, client, db_session_factory) -> None:
        for i, amount in enumerate([100, 500, 300, 200, 400, 50], start=1):
            _seed_sync(
                db_session_factory, total=float(amount), customer_name=f"Customer {i}", extraction_source="pdf_text",
            )

        top = client.get("/api/v1/invoices/sales/summary").json()["top_customers"]

        assert len(top) == 5
        assert [c["total"] for c in top] == sorted([c["total"] for c in top], reverse=True)
        assert top[0]["customer_name"] == "Customer 2"  # the 500 one

    def test_a_blank_customer_name_is_grouped_as_unattributed(self, client, db_session_factory) -> None:
        _seed_sync(db_session_factory, customer_name=None, total=100.0, extraction_source="pdf_text")
        _seed_sync(db_session_factory, customer_name="   ", total=50.0, extraction_source="pdf_text")

        top = client.get("/api/v1/invoices/sales/summary").json()["top_customers"]

        assert len(top) == 1
        assert top[0]["customer_name"] == "Unattributed"
        assert top[0]["total"] == 150.0

    def test_today_revenue_only_counts_sales_dated_today(self, client, db_session_factory) -> None:
        _seed_sync(
            db_session_factory, total=20_000.0, invoice_date=date.today(), extraction_source="pdf_text",
        )
        _seed_sync(
            db_session_factory, total=500.0, invoice_date=date(2020, 1, 1), extraction_source="pdf_text",
        )
        assert client.get("/api/v1/invoices/sales/summary").json()["totals"]["today_revenue"] == 20_000.0

    def test_today_revenue_falls_back_to_the_scan_date_like_the_monthly_bucket(
        self, client, db_session_factory,
    ) -> None:
        """No invoice_date extracted — bucketed by created_at instead, same
        fallback the monthly chart already relies on."""
        _seed_sync(db_session_factory, total=15_000.0, invoice_date=None, extraction_source="pdf_text")
        assert client.get("/api/v1/invoices/sales/summary").json()["totals"]["today_revenue"] == 15_000.0

    def test_date_range_scopes_revenue_and_document_count(self, client, db_session_factory) -> None:
        """Added for Reports Service — its Profit & Loss/Sales Report call
        this with a specific period rather than all-time."""
        _seed_sync(db_session_factory, total=1000.0, invoice_date=date(2026, 6, 15), extraction_source="pdf_text")
        _seed_sync(db_session_factory, total=500.0, invoice_date=date(2026, 7, 15), extraction_source="pdf_text")

        body = client.get(
            "/api/v1/invoices/sales/summary?date_from=2026-07-01&date_to=2026-07-31"
        ).json()

        assert body["totals"]["revenue"] == 500.0
        assert body["totals"]["document_count"] == 1
        assert body["monthly"] == [{"month": "2026-07", "total": 500.0, "count": 1}]

    def test_a_row_with_no_resolvable_date_is_excluded_once_a_range_is_given(self, client, db_session_factory) -> None:
        async def _seed_no_date():
            async with db_session_factory() as db:
                invoice = Invoice(
                    company_id=COMPANY_ID, type=InvoiceType.sale, status=InvoiceStatus.processed,
                    customer_name="X", total=1000.0, invoice_date=None, created_at=None,
                    document_confidence=0.9, extraction_source="pdf_text", review_flags=[],
                    raw_extraction_json={}, filename="r.jpg", size=1, s3_key="k", file_hash=uuid.uuid4().hex,
                )
                db.add(invoice)
                await db.commit()
        # created_at has a server_default, so this still resolves to "now" —
        # this test documents that a range excludes it only when that
        # resolved date genuinely falls outside the window, not because
        # invoice_date specifically was null.
        import asyncio
        asyncio.get_event_loop().run_until_complete(_seed_no_date())
        body = client.get("/api/v1/invoices/sales/summary?date_from=2020-01-01&date_to=2020-01-31").json()
        assert body["totals"]["document_count"] == 0

    def test_customer_limit_controls_how_many_top_customers_come_back(self, client, db_session_factory) -> None:
        for i, amount in enumerate([100, 500, 300, 200, 400, 50], start=1):
            _seed_sync(
                db_session_factory, total=float(amount), customer_name=f"Customer {i}", extraction_source="pdf_text",
            )
        body = client.get("/api/v1/invoices/sales/summary?customer_limit=2").json()
        assert len(body["top_customers"]) == 2

    def test_authored_sales_invoices_do_not_affect_the_summary(self, client, db_session_factory) -> None:
        _seed_sync(db_session_factory, total=1000.0, extraction_source="generated")
        assert client.get("/api/v1/invoices/sales/summary").json()["totals"]["revenue"] == 0.0

    def test_no_scanned_sales_yet_is_a_valid_empty_summary(self, client) -> None:
        response = client.get("/api/v1/invoices/sales/summary")
        assert response.status_code == 200
        body = response.json()
        assert body == {
            "monthly": [], "top_customers": [],
            "totals": {"revenue": 0.0, "document_count": 0, "pending_review": 0, "today_revenue": 0.0},
        }

    def test_summary_is_not_parsed_as_an_invoice_id(self, client) -> None:
        assert client.get("/api/v1/invoices/sales/summary").status_code == 200
