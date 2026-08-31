"""Invoice Generator — the sales half of architecture report §5.3.

The scanner's tests are about reading a document faithfully; these are about
the opposite guarantee: that authored numbers are recorded and totalled
exactly, and that generated invoices stay separate from scanned ones.
"""
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.tenancy import get_company_id
from app.db.session import get_db
from app.main import app
from app.models import Invoice, InvoiceStatus, InvoiceType
from app.models.base import Base
from app.services.settings_service_client import SettingsServiceError

COMPANY_ID = uuid.uuid4()
OTHER_COMPANY_ID = uuid.uuid4()

# The smallest possible valid PNG (1x1 transparent pixel) — enough for
# ReportLab's ImageReader to actually decode a real image, not a stub.
PNG_1X1_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)

VALID_PAYLOAD = {
    "customer_name": "ABC Traders",
    "invoice_number": "INV-2026-001",
    "invoice_date": "2026-08-26",
    "ntn": "3947261-8",
    "tax_rate": 0.17,
    "items": [
        {"description": "Steel Sheets", "qty": 2, "rate": 9800},
        {"description": "Welding Rods", "qty": 6, "rate": 3200},
    ],
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


class TestCreate:
    def test_totals_are_computed_from_the_line_items(self, client) -> None:
        """2*9800 + 6*3200 = 38800, +17% tax = 45396."""
        body = client.post("/api/v1/invoices/sales/", json=VALID_PAYLOAD).json()
        assert body["subtotal"] == 38800.0
        assert body["tax_amount"] == pytest.approx(6596.0)
        assert body["total"] == pytest.approx(45396.0)

    def test_a_client_supplied_total_cannot_override_the_computed_one(self, client) -> None:
        """Accepting a caller's total would let subtotal/tax/total disagree
        with the lines they summarise — the very inconsistency the scanner's
        arithmetic validation exists to catch on the purchase side."""
        payload = {**VALID_PAYLOAD, "subtotal": 1, "tax_amount": 1, "total": 1}
        body = client.post("/api/v1/invoices/sales/", json=payload).json()
        assert body["total"] == pytest.approx(45396.0)

    def test_a_generated_invoice_starts_validated_not_needing_review(self, client) -> None:
        """Its values were authored, not inferred — there is no extraction to
        second-guess, so it starts where a scanned invoice only arrives after
        a human confirms it."""
        body = client.post("/api/v1/invoices/sales/", json=VALID_PAYLOAD).json()
        assert body["status"] == InvoiceStatus.validated.value
        assert body["type"] == InvoiceType.sale.value

    def test_line_amounts_are_derived_per_row(self, client) -> None:
        items = client.post("/api/v1/invoices/sales/", json=VALID_PAYLOAD).json()["items"]
        assert [i["amount"] for i in items] == [19600.0, 19200.0]
        assert [i["position"] for i in items] == [0, 1]

    def test_an_invoice_with_no_items_is_rejected(self, client) -> None:
        payload = {**VALID_PAYLOAD, "items": []}
        assert client.post("/api/v1/invoices/sales/", json=payload).status_code == 422

    def test_a_zero_quantity_line_is_rejected(self, client) -> None:
        payload = {**VALID_PAYLOAD, "items": [{"description": "x", "qty": 0, "rate": 10}]}
        assert client.post("/api/v1/invoices/sales/", json=payload).status_code == 422

    def test_a_blank_customer_name_is_rejected(self, client) -> None:
        payload = {**VALID_PAYLOAD, "customer_name": "   "}
        assert client.post("/api/v1/invoices/sales/", json=payload).status_code == 422

    def test_a_tax_rate_above_one_is_rejected(self, client) -> None:
        """tax_rate is fractional (0.17 = 17%), matching how the scanner
        stores it — 17 would silently mean 1700%."""
        payload = {**VALID_PAYLOAD, "tax_rate": 17}
        assert client.post("/api/v1/invoices/sales/", json=payload).status_code == 422


class TestListingSeparation:
    def test_sales_invoices_do_not_appear_in_the_purchase_list(self, client) -> None:
        """The Scanner's list is about documents that were *received*; a
        generated invoice appearing there would be misleading."""
        client.post("/api/v1/invoices/sales/", json=VALID_PAYLOAD)

        purchases = client.get("/api/v1/invoices/").json()
        assert purchases["total"] == 0

        sales = client.get("/api/v1/invoices/sales/").json()
        assert sales["total"] == 1

    def test_type_all_returns_both(self, client) -> None:
        client.post("/api/v1/invoices/sales/", json=VALID_PAYLOAD)
        assert client.get("/api/v1/invoices/?type=all").json()["total"] == 1

    def test_an_unknown_type_is_rejected(self, client) -> None:
        assert client.get("/api/v1/invoices/?type=nonsense").status_code == 400


class TestRouteOrdering:
    def test_sales_is_not_swallowed_by_the_invoice_id_route(self, client) -> None:
        """/invoices/sales must match before /invoices/{invoice_id}, which
        would otherwise try to parse "sales" as a UUID and 422."""
        assert client.get("/api/v1/invoices/sales/").status_code == 200


class TestTenancy:
    def test_another_company_s_sales_invoice_is_not_visible(self, client, db_session_factory) -> None:
        import asyncio

        created = client.post("/api/v1/invoices/sales/", json=VALID_PAYLOAD).json()

        async def _reassign():
            async with db_session_factory() as session:
                invoice = await session.get(Invoice, uuid.UUID(created["id"]))
                invoice.company_id = OTHER_COMPANY_ID
                await session.commit()

        asyncio.get_event_loop().run_until_complete(_reassign())

        assert client.get(f"/api/v1/invoices/sales/{created['id']}").status_code == 404
        assert client.get("/api/v1/invoices/sales/").json()["total"] == 0


class TestPdf:
    def test_a_real_pdf_is_rendered(self, client) -> None:
        created = client.post("/api/v1/invoices/sales/", json=VALID_PAYLOAD).json()
        response = client.get(f"/api/v1/invoices/sales/{created['id']}/pdf")

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        # Rendered, not a stub: a real PDF starts with the %PDF- magic bytes.
        assert response.content.startswith(b"%PDF-")
        assert len(response.content) > 1000

    def test_the_filename_carries_the_invoice_number(self, client) -> None:
        created = client.post("/api/v1/invoices/sales/", json=VALID_PAYLOAD).json()
        response = client.get(f"/api/v1/invoices/sales/{created['id']}/pdf")
        assert "invoice-INV-2026-001.pdf" in response.headers["content-disposition"]

    def test_renders_with_a_non_default_template_and_a_logo(self, client) -> None:
        """Branding fetched from Settings Service, patched here — this is
        about the route wiring it through, not about that service being up."""
        created = client.post("/api/v1/invoices/sales/", json=VALID_PAYLOAD).json()
        branding = {
            "name": "Khan Enterprises", "invoice_template": "midnight",
            "logo_url": "data:image/png;base64," + PNG_1X1_BASE64, "logo_placement": "center",
        }
        with patch(
            "app.api.routes.sales.fetch_company_branding", AsyncMock(return_value=branding),
        ):
            response = client.get(f"/api/v1/invoices/sales/{created['id']}/pdf")
        assert response.status_code == 200
        assert response.content.startswith(b"%PDF-")

    def test_an_unreachable_settings_service_still_renders_a_plain_pdf(self, client) -> None:
        created = client.post("/api/v1/invoices/sales/", json=VALID_PAYLOAD).json()
        with patch(
            "app.api.routes.sales.fetch_company_branding",
            AsyncMock(side_effect=SettingsServiceError("down")),
        ):
            response = client.get(f"/api/v1/invoices/sales/{created['id']}/pdf")
        assert response.status_code == 200
        assert response.content.startswith(b"%PDF-")

    def test_a_purchase_invoice_cannot_be_rendered_through_the_sales_template(
        self, client, db_session_factory,
    ) -> None:
        """A scanned invoice's numbers came from OCR; presenting them through
        an authored-document template would dress guesses up as fact."""
        import asyncio

        created = client.post("/api/v1/invoices/sales/", json=VALID_PAYLOAD).json()

        async def _make_purchase():
            async with db_session_factory() as session:
                invoice = await session.get(Invoice, uuid.UUID(created["id"]))
                invoice.type = InvoiceType.purchase
                await session.commit()

        asyncio.get_event_loop().run_until_complete(_make_purchase())

        assert client.get(f"/api/v1/invoices/sales/{created['id']}/pdf").status_code == 404

    def test_an_invoice_with_no_number_still_renders(self, client) -> None:
        """Falls back to the id rather than producing `invoice-.pdf`."""
        payload = {k: v for k, v in VALID_PAYLOAD.items() if k != "invoice_number"}
        created = client.post("/api/v1/invoices/sales/", json=payload).json()
        response = client.get(f"/api/v1/invoices/sales/{created['id']}/pdf")
        assert response.status_code == 200
        assert created["id"] in response.headers["content-disposition"]
