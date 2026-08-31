"""Report routes — creation for all six types, listing/detail, tenancy
scoping, the 502-on-upstream-failure behaviour, and PDF/Excel download.
`generate_*` is patched throughout — this is about the route layer's own
persistence/error-handling, not report_generator's arithmetic (covered by
test_report_generator.py).
"""
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.tenancy import get_company_id
from app.db.session import get_db
from app.main import app
from app.models.base import Base
from app.schemas.payload import ReportPayload, ReportTable, SummaryLine
from app.services.invoice_service_client import InvoiceServiceError
from app.services.settings_service_client import SettingsServiceError

COMPANY_ID = uuid.uuid4()
OTHER_COMPANY_ID = uuid.uuid4()

SAMPLE_PAYLOAD = ReportPayload(
    title="Profit & Loss Statement", period_label="01 Jul 2026 – 31 Jul 2026",
    summary=[SummaryLine(label="Revenue", value="PKR 500,000")],
    table=ReportTable(headers=["Category", "Amount"], rows=[["Rent", "PKR 50,000"]]),
    notes=["A note."],
)

PERIOD_ENDPOINTS = ["profit-loss", "cash-flow", "tax-summary", "sales", "purchases"]
PERIOD_GENERATOR_PATCH = {
    "profit-loss": "app.api.routes.reports.generate_profit_loss",
    "cash-flow": "app.api.routes.reports.generate_cash_flow",
    "tax-summary": "app.api.routes.reports.generate_tax_summary",
    "sales": "app.api.routes.reports.generate_sales_report",
    "purchases": "app.api.routes.reports.generate_purchase_report",
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


@pytest.fixture
def no_company_name():
    """Default: Settings Service reachable, no company name set yet."""
    with patch(
        "app.api.routes.reports.fetch_company_profile", AsyncMock(return_value={"name": None}),
    ) as mock:
        yield mock


@pytest.mark.parametrize("endpoint", PERIOD_ENDPOINTS)
class TestCreatePeriodReport:
    def test_creates_a_ready_report(self, client, no_company_name, endpoint) -> None:
        with patch(PERIOD_GENERATOR_PATCH[endpoint], AsyncMock(return_value=SAMPLE_PAYLOAD)):
            response = client.post(
                f"/api/v1/reports/{endpoint}", json={"period_start": "2026-07-01", "period_end": "2026-07-31"},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ready"
        assert body["payload"]["title"] == "Profit & Loss Statement"

    def test_period_start_after_period_end_is_rejected(self, client, endpoint) -> None:
        response = client.post(
            f"/api/v1/reports/{endpoint}", json={"period_start": "2026-07-31", "period_end": "2026-07-01"},
        )
        assert response.status_code == 422

    def test_an_upstream_failure_is_a_502_and_saves_nothing(self, client, no_company_name, endpoint) -> None:
        with patch(PERIOD_GENERATOR_PATCH[endpoint], AsyncMock(side_effect=InvoiceServiceError("down"))):
            response = client.post(
                f"/api/v1/reports/{endpoint}", json={"period_start": "2026-07-01", "period_end": "2026-07-31"},
            )
        assert response.status_code == 502
        assert client.get("/api/v1/reports/").json()["total"] == 0


class TestCreateBalanceSheet:
    def test_creates_a_ready_report(self, client, no_company_name) -> None:
        with patch("app.api.routes.reports.generate_balance_sheet", AsyncMock(return_value=SAMPLE_PAYLOAD)):
            response = client.post("/api/v1/reports/balance-sheet", json={"as_of_date": "2026-07-31"})
        assert response.status_code == 200
        assert response.json()["period_start"] == response.json()["period_end"] == "2026-07-31"


class TestCompanyNameAttachment:
    def test_a_settings_service_failure_does_not_block_report_creation(self, client) -> None:
        """Best-effort only — see routes/reports.py's own docstring."""
        with (
            patch("app.api.routes.reports.generate_profit_loss", AsyncMock(return_value=SAMPLE_PAYLOAD)),
            patch(
                "app.api.routes.reports.fetch_company_profile",
                AsyncMock(side_effect=SettingsServiceError("down")),
            ),
        ):
            response = client.post(
                "/api/v1/reports/profit-loss", json={"period_start": "2026-07-01", "period_end": "2026-07-31"},
            )
        assert response.status_code == 200
        assert response.json()["payload"]["company_name"] is None


class TestListAndGet:
    def _seed(self, client, no_company_name) -> str:
        with patch("app.api.routes.reports.generate_profit_loss", AsyncMock(return_value=SAMPLE_PAYLOAD)):
            response = client.post(
                "/api/v1/reports/profit-loss", json={"period_start": "2026-07-01", "period_end": "2026-07-31"},
            )
        return response.json()["id"]

    def test_only_this_company_s_reports_are_listed(self, client, db_session_factory, no_company_name) -> None:
        self._seed(client, no_company_name)

        async def _db_other():
            async with db_session_factory() as session:
                yield session

        app.dependency_overrides[get_db] = _db_other
        app.dependency_overrides[get_company_id] = lambda: OTHER_COMPANY_ID
        other_client = TestClient(app)
        assert other_client.get("/api/v1/reports/").json()["total"] == 0

    def test_filter_by_type(self, client, no_company_name) -> None:
        self._seed(client, no_company_name)
        assert client.get("/api/v1/reports/?type=profit_loss").json()["total"] == 1
        assert client.get("/api/v1/reports/?type=cash_flow").json()["total"] == 0

    def test_an_unknown_type_is_a_400(self, client) -> None:
        assert client.get("/api/v1/reports/?type=bogus").status_code == 400

    def test_get_a_missing_report_is_a_404(self, client) -> None:
        assert client.get(f"/api/v1/reports/{uuid.uuid4()}").status_code == 404

    def test_getting_another_company_s_report_is_a_404(self, client, db_session_factory, no_company_name) -> None:
        report_id = self._seed(client, no_company_name)

        async def _db_other():
            async with db_session_factory() as session:
                yield session

        app.dependency_overrides[get_db] = _db_other
        app.dependency_overrides[get_company_id] = lambda: OTHER_COMPANY_ID
        other_client = TestClient(app)
        assert other_client.get(f"/api/v1/reports/{report_id}").status_code == 404


class TestDownloads:
    def _seed(self, client, no_company_name) -> str:
        with patch("app.api.routes.reports.generate_profit_loss", AsyncMock(return_value=SAMPLE_PAYLOAD)):
            response = client.post(
                "/api/v1/reports/profit-loss", json={"period_start": "2026-07-01", "period_end": "2026-07-31"},
            )
        return response.json()["id"]

    def test_pdf_download(self, client, no_company_name) -> None:
        report_id = self._seed(client, no_company_name)
        response = client.get(f"/api/v1/reports/{report_id}/pdf")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert response.content.startswith(b"%PDF-")

    def test_excel_download(self, client, no_company_name) -> None:
        report_id = self._seed(client, no_company_name)
        response = client.get(f"/api/v1/reports/{report_id}/excel")
        assert response.status_code == 200
        assert response.content.startswith(b"PK\x03\x04")

    def test_pdf_for_a_missing_report_is_a_404(self, client) -> None:
        assert client.get(f"/api/v1/reports/{uuid.uuid4()}/pdf").status_code == 404
