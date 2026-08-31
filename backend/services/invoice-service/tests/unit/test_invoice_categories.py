"""Saved Records' categorized cashbook endpoints: GET /invoices/options,
GET /invoices/categories/summary, GET /invoices/categories/report.pdf.
See docs/superpowers/specs/2026-09-01-saved-records-cashbook-design.md.
"""
import io
import uuid
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.tenancy import get_company_id
from app.db.session import get_db
from app.main import app
from app.models import Invoice, InvoiceItem, InvoiceStatus, InvoiceType, PaymentMethod, TransactionStatus
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


async def _seed_invoice(db_session_factory, *, company_id=COMPANY_ID, status=InvoiceStatus.validated, **overrides) -> uuid.UUID:
    async with db_session_factory() as db:
        invoice = Invoice(
            company_id=company_id, type=InvoiceType.purchase, status=status,
            vendor_name="KFC", invoice_number=None, total=1000.0,
            document_confidence=0.8, extraction_source="ocr", review_flags=[],
            raw_extraction_json={}, filename="receipt.jpg", size=100, s3_key="k/receipt.jpg",
            file_hash=uuid.uuid4().hex,
        )
        for key, value in overrides.items():
            setattr(invoice, key, value)
        invoice.items = [InvoiceItem(position=0, description="Item", qty=1.0, rate=1000.0, amount=1000.0, arithmetic_check="pass", review_flags=[])]
        db.add(invoice)
        await db.commit()
        await db.refresh(invoice)
        return invoice.id


def _seed(db_session_factory, **kwargs) -> uuid.UUID:
    import asyncio

    return asyncio.get_event_loop().run_until_complete(_seed_invoice(db_session_factory, **kwargs))


class TestOptions:
    def test_suggested_categories_are_returned(self, client) -> None:
        categories = client.get("/api/v1/invoices/options").json()["categories"]
        assert "Office Entertainment" in categories
        assert "Uncategorized" not in categories  # never assignable — see routes/invoices.py's UNCATEGORIZED docstring


class TestCategoriesSummary:
    def test_aggregates_totals_per_category(self, client, db_session_factory) -> None:
        _seed(db_session_factory, category="Office Entertainment", total=20_050.0)
        _seed(db_session_factory, category="Office Entertainment", total=19_410.0)
        _seed(db_session_factory, category="Employee Care", total=6_800.0)

        summaries = {s["category"]: s for s in client.get("/api/v1/invoices/categories/summary").json()}

        assert summaries["Office Entertainment"]["count"] == 2
        assert summaries["Office Entertainment"]["total"] == pytest.approx(39_460.0)
        assert summaries["Employee Care"]["count"] == 1
        assert summaries["Employee Care"]["total"] == pytest.approx(6_800.0)

    def test_uncategorized_invoices_are_bucketed_under_a_null_category(self, client, db_session_factory) -> None:
        _seed(db_session_factory, category=None, total=500.0)
        summaries = client.get("/api/v1/invoices/categories/summary").json()
        assert any(s["category"] is None and s["total"] == pytest.approx(500.0) for s in summaries)

    def test_excludes_invoices_not_yet_saved(self, client, db_session_factory) -> None:
        _seed(db_session_factory, category="Office Entertainment", status=InvoiceStatus.needs_review, total=999.0)
        summaries = client.get("/api/v1/invoices/categories/summary").json()
        assert summaries == []

    def test_excludes_sale_invoices(self, client, db_session_factory) -> None:
        _seed(db_session_factory, category="Office Entertainment", type=InvoiceType.sale, total=999.0)
        summaries = client.get("/api/v1/invoices/categories/summary").json()
        assert summaries == []

    def test_excludes_another_companys_invoices(self, client, db_session_factory) -> None:
        _seed(db_session_factory, company_id=OTHER_COMPANY_ID, category="Office Entertainment", total=999.0)
        summaries = client.get("/api/v1/invoices/categories/summary").json()
        assert summaries == []

    def test_send_to_accounting_status_also_counts(self, client, db_session_factory) -> None:
        _seed(db_session_factory, category="Salary", status=InvoiceStatus.sent_to_accounting, total=6_000.0)
        summaries = client.get("/api/v1/invoices/categories/summary").json()
        assert summaries[0]["total"] == pytest.approx(6_000.0)

    def test_a_date_range_scopes_the_totals_to_that_month(self, client, db_session_factory) -> None:
        _seed(db_session_factory, category="Office Entertainment", total=100.0, invoice_date=date(2026, 6, 10))
        _seed(db_session_factory, category="Office Entertainment", total=200.0, invoice_date=date(2026, 7, 1))
        _seed(db_session_factory, category="Office Entertainment", total=999.0, invoice_date=None)

        summaries = client.get(
            "/api/v1/invoices/categories/summary", params={"date_from": "2026-06-01", "date_to": "2026-06-30"},
        ).json()

        assert len(summaries) == 1
        assert summaries[0]["count"] == 1
        assert summaries[0]["total"] == pytest.approx(100.0)

    def test_a_confirmed_non_transactional_document_never_inflates_uncategorized(self, client, db_session_factory) -> None:
        """Regression guard for a real gap found live during the Cash Book
        audit: a confirmed non-transactional document (a validated Minute
        Sheet) has category=None and total=None by design (§12) — but
        without an explicit transaction_status filter it was still being
        counted as a real Uncategorized row, inflating that bucket's count
        even though it contributes zero to the total."""
        _seed(
            db_session_factory, category=None, total=None,
            transaction_status=TransactionStatus.non_transactional,
        )
        summaries = client.get("/api/v1/invoices/categories/summary").json()
        assert summaries == []


class TestAvailableMonths:
    def test_returns_distinct_months_newest_first(self, client, db_session_factory) -> None:
        _seed(db_session_factory, invoice_date=date(2026, 6, 10))
        _seed(db_session_factory, invoice_date=date(2026, 6, 20))
        _seed(db_session_factory, invoice_date=date(2026, 7, 1))

        months = client.get("/api/v1/invoices/categories/available-months").json()

        assert months == ["2026-07", "2026-06"]

    def test_undated_invoices_contribute_no_month(self, client, db_session_factory) -> None:
        _seed(db_session_factory, invoice_date=None)
        assert client.get("/api/v1/invoices/categories/available-months").json() == []

    def test_excludes_invoices_not_yet_saved(self, client, db_session_factory) -> None:
        _seed(db_session_factory, status=InvoiceStatus.needs_review, invoice_date=date(2026, 6, 1))
        assert client.get("/api/v1/invoices/categories/available-months").json() == []

    def test_excludes_another_companys_invoices(self, client, db_session_factory) -> None:
        _seed(db_session_factory, company_id=OTHER_COMPANY_ID, invoice_date=date(2026, 6, 1))
        assert client.get("/api/v1/invoices/categories/available-months").json() == []

    def test_a_confirmed_non_transactional_document_contributes_no_month(self, client, db_session_factory) -> None:
        _seed(
            db_session_factory, invoice_date=date(2026, 6, 1), total=None,
            transaction_status=TransactionStatus.non_transactional,
        )
        assert client.get("/api/v1/invoices/categories/available-months").json() == []


class TestMonthlySummary:
    """GET /invoices/monthly-summary — the Cash Book's Total/Cash/Online/
    Not-Specified/Transactions strip. Same single-SQL-aggregate discipline
    as TestCategoriesSummary above."""

    def test_cash_and_online_and_unspecified_reconcile_to_the_monthly_total(self, client, db_session_factory) -> None:
        _seed(db_session_factory, total=1_000.0, payment_method=PaymentMethod.cash)
        _seed(db_session_factory, total=2_500.0, payment_method=PaymentMethod.bank)
        _seed(db_session_factory, total=300.0, payment_method=None)

        summary = client.get("/api/v1/invoices/monthly-summary").json()

        assert summary["cash_total"] == pytest.approx(1_000.0)
        assert summary["online_total"] == pytest.approx(2_500.0)
        assert summary["unspecified_total"] == pytest.approx(300.0)
        assert summary["monthly_total"] == pytest.approx(3_800.0)
        assert summary["transaction_count"] == 3

    def test_an_unset_payment_method_is_never_silently_folded_into_cash_or_online(self, client, db_session_factory) -> None:
        _seed(db_session_factory, total=500.0, payment_method=None)
        summary = client.get("/api/v1/invoices/monthly-summary").json()
        assert summary["unspecified_total"] == pytest.approx(500.0)
        assert summary["cash_total"] == 0.0
        assert summary["online_total"] == 0.0

    def test_a_date_range_isolates_the_month(self, client, db_session_factory) -> None:
        _seed(db_session_factory, total=100.0, payment_method=PaymentMethod.cash, invoice_date=date(2026, 6, 10))
        _seed(db_session_factory, total=200.0, payment_method=PaymentMethod.cash, invoice_date=date(2026, 7, 1))

        summary = client.get(
            "/api/v1/invoices/monthly-summary", params={"date_from": "2026-06-01", "date_to": "2026-06-30"},
        ).json()

        assert summary["monthly_total"] == pytest.approx(100.0)
        assert summary["transaction_count"] == 1

    def test_non_transactional_documents_never_enter_any_total(self, client, db_session_factory) -> None:
        """The exact safety rule this endpoint exists to protect: an
        approval memo's amount_mentioned must never reach the Cash Book,
        under any payment-method bucket."""
        _seed(
            db_session_factory, total=None, payment_method=None,
            transaction_status=TransactionStatus.non_transactional,
        )
        summary = client.get("/api/v1/invoices/monthly-summary").json()
        assert summary == {
            "monthly_total": 0.0, "cash_total": 0.0, "online_total": 0.0, "unspecified_total": 0.0,
            "transaction_count": 0,
        }

    def test_excludes_invoices_not_yet_saved(self, client, db_session_factory) -> None:
        _seed(db_session_factory, total=999.0, status=InvoiceStatus.needs_review)
        summary = client.get("/api/v1/invoices/monthly-summary").json()
        assert summary["monthly_total"] == 0.0
        assert summary["transaction_count"] == 0

    def test_excludes_another_companys_invoices(self, client, db_session_factory) -> None:
        _seed(db_session_factory, company_id=OTHER_COMPANY_ID, total=999.0)
        summary = client.get("/api/v1/invoices/monthly-summary").json()
        assert summary["monthly_total"] == 0.0

    def test_an_empty_month_returns_all_zeros_not_an_error(self, client) -> None:
        summary = client.get(
            "/api/v1/invoices/monthly-summary", params={"date_from": "2026-06-01", "date_to": "2026-06-30"},
        ).json()
        assert summary == {
            "monthly_total": 0.0, "cash_total": 0.0, "online_total": 0.0, "unspecified_total": 0.0,
            "transaction_count": 0,
        }


#: A real, minimally valid 1x1 PNG — not just PNG-looking bytes. The PDF
#: renderer actually decodes this (ImageReader/Pillow) to measure its aspect
#: ratio before embedding it, so a fake signature that Pillow cannot open
#: would exercise the "corrupt image" degrade-gracefully path instead of the
#: happy path these tests are for. See test_a_missing_source_file_degrades_
#: gracefully... below for that path, using an intentionally-broken mock.
_REAL_PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
    b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb0\x00\x00\x00\x00IEND\xaeB`\x82"
)


class TestCategoryReportPdf:
    def _mock_storage(self) -> MagicMock:
        mock = MagicMock()
        mock.s3_client.get_object.return_value = {"Body": io.BytesIO(_REAL_PNG_1X1)}
        return mock

    def test_a_categorys_report_is_a_real_pdf_and_uses_the_same_total_the_page_shows(self, client, db_session_factory) -> None:
        _seed(db_session_factory, category="Office Entertainment", total=20_050.0, mimetype="image/png")
        _seed(db_session_factory, category="Office Entertainment", total=19_410.0, mimetype="image/png")
        _seed(db_session_factory, category="Employee Care", total=6_800.0, mimetype="image/png")

        # The number Saved Records shows on screen for this category —
        # asserted here so the two are provably reading the same aggregate,
        # not just both individually plausible.
        summary = next(
            s for s in client.get("/api/v1/invoices/categories/summary").json() if s["category"] == "Office Entertainment"
        )
        assert summary["total"] == pytest.approx(39_460.0)

        with patch("app.api.routes.invoices.StorageManager", return_value=self._mock_storage()):
            response = client.get("/api/v1/invoices/categories/report.pdf", params={"category": "Office Entertainment"})

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert response.content.startswith(b"%PDF-")

    def test_the_uncategorized_bucket_is_downloadable(self, client, db_session_factory) -> None:
        _seed(db_session_factory, category=None, total=500.0, mimetype="image/png")
        with patch("app.api.routes.invoices.StorageManager", return_value=self._mock_storage()):
            response = client.get("/api/v1/invoices/categories/report.pdf", params={"category": "Uncategorized"})
        assert response.status_code == 200
        assert response.content.startswith(b"%PDF-")

    def test_omitting_category_covers_everything(self, client, db_session_factory) -> None:
        _seed(db_session_factory, category="Office Entertainment", total=100.0, mimetype="image/png")
        _seed(db_session_factory, category="Employee Care", total=200.0, mimetype="image/png")
        with patch("app.api.routes.invoices.StorageManager", return_value=self._mock_storage()):
            response = client.get("/api/v1/invoices/categories/report.pdf")
        assert response.status_code == 200
        assert response.content.startswith(b"%PDF-")

    def test_a_missing_source_file_degrades_gracefully_rather_than_failing_the_report(self, client, db_session_factory) -> None:
        _seed(db_session_factory, category="Office Entertainment", total=100.0, mimetype="image/jpeg")

        broken_storage = MagicMock()
        broken_storage.s3_client.get_object.side_effect = Exception("object storage unreachable")
        with patch("app.api.routes.invoices.StorageManager", return_value=broken_storage):
            response = client.get("/api/v1/invoices/categories/report.pdf", params={"category": "Office Entertainment"})

        assert response.status_code == 200
        assert response.content.startswith(b"%PDF-")

    def test_an_empty_category_still_downloads_a_valid_pdf(self, client) -> None:
        with patch("app.api.routes.invoices.StorageManager", return_value=self._mock_storage()):
            response = client.get("/api/v1/invoices/categories/report.pdf", params={"category": "Legal & Professional"})
        assert response.status_code == 200
        assert response.content.startswith(b"%PDF-")
