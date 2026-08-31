"""send_attachment_to_scanner — mirrors slack-connector's own
test_scanner_bridge.py test-for-test, since the two bridges are
deliberately identical in shape (see scanner_bridge.py's own docstring).
"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest

from app.core.config import Settings
from app.models import EmailAttachment
from app.services.scanner_bridge import ScannerBridgeError, send_attachment_to_scanner


def _settings() -> Settings:
    return Settings(
        GOOGLE_CLIENT_ID="id", GOOGLE_CLIENT_SECRET="secret", GOOGLE_REDIRECT_URI="http://localhost:8011/callback",
        TOKEN_ENCRYPTION_KEY="dGVzdC1mZXJuZXQta2V5LW11c3QtYmUtMzItYnl0ZXM=",
        DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost/db", REDIS_URL="redis://localhost:6379/4",
        S3_ENDPOINT_URL="http://localhost:9000", S3_ACCESS_KEY_ID="key", S3_SECRET_ACCESS_KEY="secret",
        S3_BUCKET_NAME="bucket", FRONTEND_BASE_URL="http://localhost:3000",
        INVOICE_SERVICE_URL="http://invoice-service:8002",
    )


def _attachment(**overrides) -> EmailAttachment:
    defaults = dict(
        account_id=uuid4(), message_id=uuid4(), attachment_index=0,
        provider_attachment_id="A1", filename="receipt.jpg", mimetype="image/jpeg", size=100,
        downloaded=True, s3_key="A1/receipt.jpg", created_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return EmailAttachment(**defaults)


@pytest.mark.asyncio
async def test_raises_when_attachment_not_downloaded_yet() -> None:
    attachment = _attachment(downloaded=False, s3_key=None)
    with pytest.raises(ScannerBridgeError, match="has not finished downloading"):
        await send_attachment_to_scanner(_settings(), attachment, company_id=uuid4())


@pytest.mark.asyncio
async def test_posts_attachment_bytes_to_invoice_service_scan_endpoint() -> None:
    attachment = _attachment()
    company_id = uuid4()
    fake_s3_object = {"Body": MagicMock(read=MagicMock(return_value=b"fake-image-bytes"))}

    with patch("app.services.scanner_bridge.DownloadManager") as MockDownloadManager:
        MockDownloadManager.return_value.s3_client.get_object.return_value = fake_s3_object
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = {"job_id": "abc-123", "status": "processing"}
        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)) as mock_post:
            result = await send_attachment_to_scanner(_settings(), attachment, company_id)

    assert result == {"job_id": "abc-123", "status": "processing"}
    call_kwargs = mock_post.call_args.kwargs
    assert call_kwargs["headers"]["X-Company-ID"] == str(company_id)
    assert call_kwargs["files"]["file"][0] == "receipt.jpg"


@pytest.mark.asyncio
async def test_invoice_type_sale_posts_to_the_revenue_manager_endpoint() -> None:
    """Revenue Manager's scan-to-revenue path (design spec
    docs/superpowers/specs/2026-08-27-revenue-manager-scan-design.md
    §7.1): same attachment, different Invoice Service URL, no `type` form
    field — that endpoint is sale-only."""
    attachment = _attachment()
    fake_s3_object = {"Body": MagicMock(read=MagicMock(return_value=b"bytes"))}

    with patch("app.services.scanner_bridge.DownloadManager") as MockDownloadManager:
        MockDownloadManager.return_value.s3_client.get_object.return_value = fake_s3_object
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = {"job_id": "abc-123", "status": "done"}
        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)) as mock_post:
            await send_attachment_to_scanner(_settings(), attachment, company_id=uuid4(), invoice_type="sale")

    call_args = mock_post.call_args
    assert call_args.args[0] == "http://invoice-service:8002/api/v1/invoices/sales/scan"
    assert call_args.kwargs["data"] == {}


@pytest.mark.asyncio
async def test_invoice_type_purchase_is_unaffected_by_the_new_parameter() -> None:
    attachment = _attachment()
    fake_s3_object = {"Body": MagicMock(read=MagicMock(return_value=b"bytes"))}

    with patch("app.services.scanner_bridge.DownloadManager") as MockDownloadManager:
        MockDownloadManager.return_value.s3_client.get_object.return_value = fake_s3_object
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = {"job_id": "abc-123", "status": "processing"}
        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)) as mock_post:
            await send_attachment_to_scanner(_settings(), attachment, company_id=uuid4())

    call_args = mock_post.call_args
    assert call_args.args[0] == "http://invoice-service:8002/api/v1/invoices/scan"
    assert call_args.kwargs["data"] == {"type": "purchase"}


@pytest.mark.asyncio
async def test_raises_scanner_bridge_error_when_invoice_service_rejects_it() -> None:
    attachment = _attachment()
    fake_s3_object = {"Body": MagicMock(read=MagicMock(return_value=b"bytes"))}

    with patch("app.services.scanner_bridge.DownloadManager") as MockDownloadManager:
        MockDownloadManager.return_value.s3_client.get_object.return_value = fake_s3_object
        mock_response = MagicMock(status_code=502, text="Invoice Service unavailable")
        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)):
            with pytest.raises(ScannerBridgeError, match="Invoice Service rejected"):
                await send_attachment_to_scanner(_settings(), attachment, company_id=uuid4())


@pytest.mark.asyncio
async def test_connection_error_becomes_actionable_error() -> None:
    attachment = _attachment()
    fake_s3_object = {"Body": MagicMock(read=MagicMock(return_value=b"bytes"))}

    with patch("app.services.scanner_bridge.DownloadManager") as MockDownloadManager:
        MockDownloadManager.return_value.s3_client.get_object.return_value = fake_s3_object
        with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=httpx.ConnectError("refused"))):
            with pytest.raises(ScannerBridgeError) as excinfo:
                await send_attachment_to_scanner(_settings(), attachment, company_id=uuid4())

    message = str(excinfo.value)
    assert "Could not reach the AI Scanner" in message
    assert "invoice-service:8002" in message


@pytest.mark.asyncio
async def test_timeout_becomes_actionable_error() -> None:
    attachment = _attachment()
    fake_s3_object = {"Body": MagicMock(read=MagicMock(return_value=b"bytes"))}

    with patch("app.services.scanner_bridge.DownloadManager") as MockDownloadManager:
        MockDownloadManager.return_value.s3_client.get_object.return_value = fake_s3_object
        with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=httpx.ReadTimeout("slow"))):
            with pytest.raises(ScannerBridgeError) as excinfo:
                await send_attachment_to_scanner(_settings(), attachment, company_id=uuid4())

    message = str(excinfo.value)
    assert "did not respond in time" in message
    assert "still saved" in message


@pytest.mark.asyncio
async def test_non_json_success_response_becomes_scanner_bridge_error() -> None:
    attachment = _attachment()
    fake_s3_object = {"Body": MagicMock(read=MagicMock(return_value=b"bytes"))}

    with patch("app.services.scanner_bridge.DownloadManager") as MockDownloadManager:
        MockDownloadManager.return_value.s3_client.get_object.return_value = fake_s3_object
        mock_response = MagicMock(status_code=200)
        mock_response.json.side_effect = ValueError("not json")
        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)):
            with pytest.raises(ScannerBridgeError, match="not valid JSON"):
                await send_attachment_to_scanner(_settings(), attachment, company_id=uuid4())
