from uuid import UUID

import httpx
from shared.auth import internal_service_headers

from app.core.config import Settings
from app.models import EmailAttachment
from app.services.download_manager import DownloadManager


class ScannerBridgeError(Exception):
    """Raised when an attachment can't be handed off to Invoice Service's scan pipeline."""


async def send_attachment_to_scanner(
    settings: Settings, attachment: EmailAttachment, company_id: UUID, *, invoice_type: str = "purchase",
) -> dict:
    """Hand one stored attachment to the Invoice Service.

    Deliberately identical in shape to slack-connector's send_file_to_scanner —
    a plain authenticated HTTP POST, not a RabbitMQ publish. See
    docs/email-connector-plan.md §11b: the queue and the Invoice Service are
    in the architecture report's target design but are not built yet, so both
    connectors use the same bridge and will be upgraded together rather than
    email inventing a second, inconsistent integration now.

    `invoice_type="sale"` posts to the Revenue Manager's scan-to-revenue
    endpoint instead of the purchase Scanner's — design spec
    docs/superpowers/specs/2026-08-27-revenue-manager-scan-design.md §7.1.
    """
    if not attachment.downloaded or not attachment.s3_key:
        raise ScannerBridgeError("This attachment has not finished downloading yet")

    download_manager = DownloadManager(settings)
    s3_object = download_manager.s3_client.get_object(
        Bucket=settings.s3_bucket_name, Key=attachment.s3_key
    )
    content = s3_object["Body"].read()

    base = settings.invoice_service_url.rstrip("/")
    scan_url = f"{base}/api/v1/invoices/scan" if invoice_type == "purchase" else f"{base}/api/v1/invoices/sales/scan"

    # Minted rather than a bare X-Company-ID header — see
    # shared.auth.internal_service_headers for why the header alone gets a 401.
    headers = internal_service_headers(
        secret_key=settings.jwt_secret_key, company_id=company_id, service_name="email-connector",
    )

    try:
        # Generous: the scan this triggers runs OCR, which takes minutes on a
        # CPU-constrained host. The previous 30s cut off viable scans.
        async with httpx.AsyncClient(timeout=settings.invoice_service_timeout_seconds) as client:
            response = await client.post(
                scan_url,
                files={
                    "file": (
                        attachment.filename,
                        content,
                        attachment.mimetype or "application/octet-stream",
                    )
                },
                data={"type": "purchase"} if invoice_type == "purchase" else {},
                headers=headers,
            )
    except httpx.TimeoutException as exc:
        raise ScannerBridgeError(
            "The AI Scanner did not respond in time. The attachment is still saved — try again."
        ) from exc
    except httpx.RequestError as exc:
        # Invoice Service is not built yet, so this is the path most sends take
        # today. Without catching it the httpx error escapes as an opaque 500.
        raise ScannerBridgeError(
            f"Could not reach the AI Scanner at {scan_url}. "
            "Check the Invoice Service is running."
        ) from exc

    if response.status_code >= 400:
        raise ScannerBridgeError(
            f"Invoice Service rejected the attachment: {response.status_code} {response.text}"
        )

    try:
        return response.json()
    except ValueError as exc:
        raise ScannerBridgeError("The AI Scanner returned a response that was not valid JSON.") from exc
