from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest

from app.core.config import Settings
from app.models import File
from app.services.scanner_bridge import ScannerBridgeError, send_file_to_scanner


def _settings() -> Settings:
    return Settings(
        SLACK_CLIENT_ID="id", SLACK_CLIENT_SECRET="secret", SLACK_SIGNING_SECRET="signing",
        SLACK_REDIRECT_URI="http://localhost:8010/api/v1/slack/callback",
        TOKEN_ENCRYPTION_KEY="dGVzdC1mZXJuZXQta2V5LW11c3QtYmUtMzItYnl0ZXM=",
        DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost/db", REDIS_URL="redis://localhost:6379/3",
        S3_ENDPOINT_URL="http://localhost:9000", S3_ACCESS_KEY_ID="key", S3_SECRET_ACCESS_KEY="secret",
        S3_BUCKET_NAME="bucket", FRONTEND_BASE_URL="http://localhost:3000",
        DEFAULT_COMPANY_ID=str(uuid4()), INVOICE_SERVICE_URL="http://invoice-service:8002",
    )


def _file(**overrides) -> File:
    defaults = dict(
        installation_id=uuid4(), slack_file_id="F1", filename="invoice.pdf", file_type="pdf",
        mimetype="application/pdf", size=100, created_at=datetime.now(), is_external=False,
        slack_permalink="https://example.com", conversation_id=uuid4(), message_ts="1.1",
        downloaded=True, s3_key="F1/invoice.pdf",
    )
    defaults.update(overrides)
    return File(**defaults)


@pytest.mark.asyncio
async def test_raises_when_file_not_downloaded_yet() -> None:
    file_obj = _file(downloaded=False, s3_key=None)
    with pytest.raises(ScannerBridgeError, match="has not finished downloading"):
        await send_file_to_scanner(_settings(), file_obj, company_id=uuid4())


@pytest.mark.asyncio
async def test_posts_file_bytes_to_invoice_service_scan_endpoint() -> None:
    file_obj = _file()
    company_id = uuid4()
    fake_s3_object = {"Body": MagicMock(read=MagicMock(return_value=b"%PDF-1.4 fake bytes"))}

    with patch("app.services.scanner_bridge.DownloadManager") as MockDownloadManager:
        MockDownloadManager.return_value.s3_client.get_object.return_value = fake_s3_object
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = {"job_id": "abc-123", "status": "processing"}
        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)) as mock_post:
            result = await send_file_to_scanner(_settings(), file_obj, company_id)

    assert result == {"job_id": "abc-123", "status": "processing"}
    call_kwargs = mock_post.call_args.kwargs
    assert call_kwargs["headers"]["X-Company-ID"] == str(company_id)
    assert call_kwargs["files"]["file"][0] == "invoice.pdf"


@pytest.mark.asyncio
async def test_raises_scanner_bridge_error_when_invoice_service_rejects_it() -> None:
    file_obj = _file()
    fake_s3_object = {"Body": MagicMock(read=MagicMock(return_value=b"bytes"))}

    with patch("app.services.scanner_bridge.DownloadManager") as MockDownloadManager:
        MockDownloadManager.return_value.s3_client.get_object.return_value = fake_s3_object
        mock_response = MagicMock(status_code=502, text="Invoice Service unavailable")
        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)):
            with pytest.raises(ScannerBridgeError, match="Invoice Service rejected"):
                await send_file_to_scanner(_settings(), file_obj, company_id=uuid4())


# --- Unreachable / misbehaving Invoice Service -------------------------------
# Invoice Service is not built yet, so these are the paths every send takes
# today. Before this, httpx errors escaped uncaught and surfaced as an opaque
# 500 -- and a bulk send from the Documents page turned that into twenty of them.


@pytest.mark.asyncio
async def test_connection_error_becomes_actionable_error() -> None:
    file_obj = _file()
    fake_s3_object = {"Body": MagicMock(read=MagicMock(return_value=b"bytes"))}

    with patch("app.services.scanner_bridge.DownloadManager") as MockDownloadManager:
        MockDownloadManager.return_value.s3_client.get_object.return_value = fake_s3_object
        with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=httpx.ConnectError("refused"))):
            with pytest.raises(ScannerBridgeError) as excinfo:
                await send_file_to_scanner(_settings(), file_obj, company_id=uuid4())

    message = str(excinfo.value)
    assert "Could not reach the AI Scanner" in message
    assert "invoice-service:8002" in message, "the message should name the URL that failed"


@pytest.mark.asyncio
async def test_timeout_becomes_actionable_error() -> None:
    file_obj = _file()
    fake_s3_object = {"Body": MagicMock(read=MagicMock(return_value=b"bytes"))}

    with patch("app.services.scanner_bridge.DownloadManager") as MockDownloadManager:
        MockDownloadManager.return_value.s3_client.get_object.return_value = fake_s3_object
        with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=httpx.ReadTimeout("slow"))):
            with pytest.raises(ScannerBridgeError) as excinfo:
                await send_file_to_scanner(_settings(), file_obj, company_id=uuid4())

    message = str(excinfo.value)
    assert "did not respond in time" in message
    assert "still saved" in message, "user should know the file was not lost"


@pytest.mark.asyncio
async def test_invoice_type_sale_posts_to_the_revenue_manager_endpoint() -> None:
    """Revenue Manager's scan-to-revenue path (design spec
    docs/superpowers/specs/2026-08-27-revenue-manager-scan-design.md
    §7.1): the same file, sent to a different Invoice Service URL, with no
    `type` form field at all — that endpoint is sale-only."""
    file_obj = _file()
    fake_s3_object = {"Body": MagicMock(read=MagicMock(return_value=b"bytes"))}

    with patch("app.services.scanner_bridge.DownloadManager") as MockDownloadManager:
        MockDownloadManager.return_value.s3_client.get_object.return_value = fake_s3_object
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = {"job_id": "abc-123", "status": "done"}
        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)) as mock_post:
            await send_file_to_scanner(_settings(), file_obj, company_id=uuid4(), invoice_type="sale")

    call_args = mock_post.call_args
    assert call_args.args[0] == "http://invoice-service:8002/api/v1/invoices/sales/scan"
    assert call_args.kwargs["data"] == {}


@pytest.mark.asyncio
async def test_invoice_type_purchase_is_unaffected_by_the_new_parameter() -> None:
    """Every existing caller passes no invoice_type at all — the default
    must keep hitting the original endpoint with the original form field."""
    file_obj = _file()
    fake_s3_object = {"Body": MagicMock(read=MagicMock(return_value=b"bytes"))}

    with patch("app.services.scanner_bridge.DownloadManager") as MockDownloadManager:
        MockDownloadManager.return_value.s3_client.get_object.return_value = fake_s3_object
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = {"job_id": "abc-123", "status": "processing"}
        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)) as mock_post:
            await send_file_to_scanner(_settings(), file_obj, company_id=uuid4())

    call_args = mock_post.call_args
    assert call_args.args[0] == "http://invoice-service:8002/api/v1/invoices/scan"
    assert call_args.kwargs["data"] == {"type": "purchase"}


@pytest.mark.asyncio
async def test_non_json_success_response_becomes_scanner_bridge_error() -> None:
    file_obj = _file()
    fake_s3_object = {"Body": MagicMock(read=MagicMock(return_value=b"bytes"))}

    with patch("app.services.scanner_bridge.DownloadManager") as MockDownloadManager:
        MockDownloadManager.return_value.s3_client.get_object.return_value = fake_s3_object
        mock_response = MagicMock(status_code=200)
        mock_response.json.side_effect = ValueError("not json")
        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)):
            with pytest.raises(ScannerBridgeError, match="not valid JSON"):
                await send_file_to_scanner(_settings(), file_obj, company_id=uuid4())
