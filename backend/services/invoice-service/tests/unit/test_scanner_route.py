"""POST /invoices/scan and GET /invoices/scan/{job_id} — the AI Invoice
Scanner (architecture report §5.3). AI Engine and S3 storage are mocked
here; the real end-to-end path (a genuine AI Engine call against a real
sample invoice) is covered by live verification against the running
Docker stack, the same split this codebase uses everywhere else.
"""
import base64
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from unittest.mock import AsyncMock, patch

from app.core.tenancy import get_company_id
from app.db.session import get_db
from app.main import app
from app.models import Invoice
from app.models.base import Base
from app.schemas.ai_extraction import (
    AdditionalDocumentSchema, ArithmeticValidationSchema, ExtractedFieldSchema, ExtractedInvoiceSchema,
)
from app.services.ai_engine_client import AIEngineError

COMPANY_ID = uuid.uuid4()


def _field(value=None, confidence=1.0, method="label_anchor+pattern") -> ExtractedFieldSchema:
    return ExtractedFieldSchema(value=value, confidence=confidence, method=method)


def _extraction(**overrides) -> ExtractedInvoiceSchema:
    defaults = dict(
        vendor_name=_field("ABC Traders"), invoice_number=_field("INV-1841"), invoice_date=_field("2026-08-04"),
        ntn=_field(None), subtotal=_field(None), tax_rate=_field(None), tax_amount=_field(None),
        total=_field(183254.0), line_items=[],
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
    mock.store_bytes = AsyncMock(return_value=("job-key/invoice.pdf", "hash"))
    return mock


def _additional_document(image_bytes: bytes = b"split-doc-bytes", **overrides) -> AdditionalDocumentSchema:
    return AdditionalDocumentSchema(
        extracted=_extraction(**overrides), image_base64=base64.b64encode(image_bytes).decode(),
        filename="photo_doc_2.png", mimetype="image/png",
    )


class TestScanSuccess:
    def test_a_successful_scan_creates_an_invoice(self, client) -> None:
        with patch(
            "app.api.routes.scanner.extract_invoice_data", AsyncMock(return_value=_extraction()),
        ), patch("app.api.routes.scanner.StorageManager", return_value=_mock_storage()):
            response = client.post(
                "/api/v1/invoices/scan",
                files={"file": ("invoice.pdf", b"%PDF-fake-bytes", "application/pdf")},
                data={"type": "purchase"},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "done"
        assert body["invoice"]["vendor_name"] == "ABC Traders"
        assert body["invoice"]["total"] == 183254.0
        assert body["invoice"]["status"] == "processed"

    def test_needs_review_status_is_preserved_through_to_the_invoice(self, client) -> None:
        with patch(
            "app.api.routes.scanner.extract_invoice_data",
            AsyncMock(return_value=_extraction(review_status="needs_review_high_priority")),
        ), patch("app.api.routes.scanner.StorageManager", return_value=_mock_storage()):
            response = client.post(
                "/api/v1/invoices/scan",
                files={"file": ("invoice.pdf", b"%PDF-fake-bytes", "application/pdf")},
            )

        assert response.json()["invoice"]["status"] == "needs_review_high_priority"

    def test_scan_job_can_be_polled_afterward(self, client) -> None:
        with patch(
            "app.api.routes.scanner.extract_invoice_data", AsyncMock(return_value=_extraction()),
        ), patch("app.api.routes.scanner.StorageManager", return_value=_mock_storage()):
            scan_response = client.post(
                "/api/v1/invoices/scan", files={"file": ("invoice.pdf", b"bytes", "application/pdf")},
            )
        job_id = scan_response.json()["job_id"]

        poll_response = client.get(f"/api/v1/invoices/scan/{job_id}")

        assert poll_response.status_code == 200
        assert poll_response.json()["status"] == "done"
        assert poll_response.json()["invoice"]["vendor_name"] == "ABC Traders"


class TestScanDeduplication:
    def test_scanning_the_same_file_twice_returns_the_existing_invoice(self, client) -> None:
        """Rule 7.1 — same file content, same company: the second scan must
        not create a duplicate invoice or call AI Engine again."""
        extract_mock = AsyncMock(return_value=_extraction())
        with patch("app.api.routes.scanner.extract_invoice_data", extract_mock), \
             patch("app.api.routes.scanner.StorageManager", return_value=_mock_storage()):
            first = client.post(
                "/api/v1/invoices/scan", files={"file": ("invoice.pdf", b"identical-bytes", "application/pdf")},
            )
            second = client.post(
                "/api/v1/invoices/scan", files={"file": ("invoice.pdf", b"identical-bytes", "application/pdf")},
            )

        assert first.json()["invoice_id"] == second.json()["invoice_id"]
        assert extract_mock.call_count == 1

    def test_a_different_file_is_not_treated_as_a_duplicate(self, client) -> None:
        with patch(
            "app.api.routes.scanner.extract_invoice_data", AsyncMock(return_value=_extraction()),
        ), patch("app.api.routes.scanner.StorageManager", return_value=_mock_storage()):
            first = client.post(
                "/api/v1/invoices/scan", files={"file": ("a.pdf", b"file-a-bytes", "application/pdf")},
            )
            second = client.post(
                "/api/v1/invoices/scan", files={"file": ("b.pdf", b"file-b-bytes", "application/pdf")},
            )

        assert first.json()["invoice_id"] != second.json()["invoice_id"]


class TestScanFailureHandling:
    def test_ai_engine_unreachable_is_a_clean_502_not_a_500(self, client) -> None:
        with patch(
            "app.api.routes.scanner.extract_invoice_data",
            AsyncMock(side_effect=AIEngineError("Could not reach AI Engine")),
        ):
            response = client.post(
                "/api/v1/invoices/scan", files={"file": ("invoice.pdf", b"bytes", "application/pdf")},
            )

        assert response.status_code == 502

    def test_a_sales_invoice_upload_is_rejected(self, client) -> None:
        response = client.post(
            "/api/v1/invoices/scan",
            files={"file": ("invoice.pdf", b"bytes", "application/pdf")},
            data={"type": "sale"},
        )
        assert response.status_code == 400

    def test_a_missing_scan_job_404s(self, client) -> None:
        response = client.get(f"/api/v1/invoices/scan/{uuid.uuid4()}")
        assert response.status_code == 404

    def test_another_companys_scan_job_is_not_visible(self, client, db_session_factory) -> None:
        other_company = uuid.uuid4()
        with patch(
            "app.api.routes.scanner.extract_invoice_data", AsyncMock(return_value=_extraction()),
        ), patch("app.api.routes.scanner.StorageManager", return_value=_mock_storage()):
            scan_response = client.post(
                "/api/v1/invoices/scan", files={"file": ("invoice.pdf", b"bytes", "application/pdf")},
            )
        job_id = scan_response.json()["job_id"]

        app.dependency_overrides[get_company_id] = lambda: other_company
        response = client.get(f"/api/v1/invoices/scan/{job_id}")

        assert response.status_code == 404


class TestDocumentPreprocessingSplit:
    """One upload, multiple documents (docs/invoice-ocr-plan.md §7) — AI
    Engine's response carrying `additional_documents` fans out into one
    extra Invoice per split, each a first-class record of its own."""

    def test_no_additional_documents_means_an_empty_list(self, client) -> None:
        with patch(
            "app.api.routes.scanner.extract_invoice_data", AsyncMock(return_value=_extraction()),
        ), patch("app.api.routes.scanner.StorageManager", return_value=_mock_storage()):
            response = client.post(
                "/api/v1/invoices/scan", files={"file": ("invoice.pdf", b"bytes", "application/pdf")},
            )
        assert response.json()["additional_invoice_ids"] == []

    def test_one_additional_document_creates_a_second_invoice(self, client, db_session_factory) -> None:
        extraction = _extraction(additional_documents=[_additional_document(vendor_name=_field("Papa Johns"))])
        with patch(
            "app.api.routes.scanner.extract_invoice_data", AsyncMock(return_value=extraction),
        ), patch("app.api.routes.scanner.StorageManager", return_value=_mock_storage()):
            response = client.post(
                "/api/v1/invoices/scan", files={"file": ("photo.jpg", b"original-photo-bytes", "image/jpeg")},
            )

        body = response.json()
        assert response.status_code == 200
        assert len(body["additional_invoice_ids"]) == 1
        primary_id, split_id = body["invoice_id"], body["additional_invoice_ids"][0]
        assert primary_id != split_id

        async def _fetch_vendor(invoice_id):
            async with db_session_factory() as session:
                return (await session.execute(select(Invoice.vendor_name).where(Invoice.id == invoice_id))).scalar()

        import asyncio
        assert asyncio.get_event_loop().run_until_complete(_fetch_vendor(uuid.UUID(primary_id))) == "ABC Traders"
        assert asyncio.get_event_loop().run_until_complete(_fetch_vendor(uuid.UUID(split_id))) == "Papa Johns"

    def test_a_split_documents_storage_failure_does_not_fail_the_whole_scan(self, client) -> None:
        extraction = _extraction(additional_documents=[_additional_document()])
        storage = AsyncMock()
        storage.store_bytes = AsyncMock(
            side_effect=[("job-key/photo.jpg", "hash"), Exception("S3 unreachable for the split document")],
        )
        with patch("app.api.routes.scanner.extract_invoice_data", AsyncMock(return_value=extraction)), \
             patch("app.api.routes.scanner.StorageManager", return_value=storage):
            response = client.post(
                "/api/v1/invoices/scan", files={"file": ("photo.jpg", b"bytes", "image/jpeg")},
            )

        # The primary invoice still succeeds — a bad split is best-effort,
        # not a reason to fail an otherwise-successful scan.
        assert response.status_code == 200
        assert response.json()["invoice_id"] is not None
        assert response.json()["additional_invoice_ids"] == []

    def test_rescanning_the_same_photo_reuses_the_existing_split_invoice(self, client) -> None:
        extraction = _extraction(additional_documents=[_additional_document(image_bytes=b"same-split-bytes")])
        with patch(
            "app.api.routes.scanner.extract_invoice_data", AsyncMock(return_value=extraction),
        ), patch("app.api.routes.scanner.StorageManager", return_value=_mock_storage()):
            first = client.post(
                "/api/v1/invoices/scan", files={"file": ("a.jpg", b"photo-a-bytes", "image/jpeg")},
            )
            second = client.post(
                "/api/v1/invoices/scan", files={"file": ("b.jpg", b"photo-b-bytes", "image/jpeg")},
            )

        # Two different primary photos that happen to split out the exact
        # same crop (e.g. the same physical receipt photographed twice as
        # part of two different multi-document shots) must not duplicate
        # that split's invoice.
        assert first.json()["additional_invoice_ids"] == second.json()["additional_invoice_ids"]


class TestAutoCropPreview:
    """POST /invoices/auto-crop-preview — the camera-capture flow's own
    fast preview crop. No DB row is ever touched here (no Invoice, no
    AIJob) — this only proxies to AI Engine and hands back bytes."""

    def test_a_cropped_image_is_returned(self, client: TestClient) -> None:
        with patch(
            "app.api.routes.scanner.auto_crop_image", AsyncMock(return_value=b"cropped-png-bytes"),
        ):
            response = client.post(
                "/api/v1/invoices/auto-crop-preview",
                files={"file": ("capture.jpg", b"fake-photo-bytes", "image/jpeg")},
            )

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content == b"cropped-png-bytes"

    def test_an_ai_engine_failure_is_a_clean_502(self, client: TestClient) -> None:
        with patch(
            "app.api.routes.scanner.auto_crop_image",
            AsyncMock(side_effect=AIEngineError("AI Engine could not process this image")),
        ):
            response = client.post(
                "/api/v1/invoices/auto-crop-preview",
                files={"file": ("capture.jpg", b"fake-photo-bytes", "image/jpeg")},
            )

        assert response.status_code == 502

    def test_an_oversized_file_is_rejected_before_calling_ai_engine(self, client: TestClient, monkeypatch) -> None:
        from app.core.config import get_settings

        get_settings.cache_clear()
        monkeypatch.setenv("MAX_UPLOAD_SIZE_MB", "0")
        try:
            response = client.post(
                "/api/v1/invoices/auto-crop-preview",
                files={"file": ("capture.jpg", b"fake-photo-bytes", "image/jpeg")},
            )
            assert response.status_code == 413
        finally:
            monkeypatch.undo()
            get_settings.cache_clear()
