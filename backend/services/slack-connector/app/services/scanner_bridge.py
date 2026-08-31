from uuid import UUID

import httpx
from shared.auth import internal_service_headers

from app.core.config import Settings
from app.models import File
from app.services.download_manager import DownloadManager


class ScannerBridgeError(Exception):
    """Raised when a Slack file can't be handed off to Invoice Service's scan pipeline."""


async def send_file_to_scanner(
    settings: Settings, file_obj: File, company_id: UUID, *, invoice_type: str = "purchase",
) -> dict:
    """`invoice_type="purchase"` (default — every existing caller) posts to
    Invoice Service's original scanner unchanged. `invoice_type="sale"`
    posts to the Revenue Manager's scan-to-revenue endpoint instead (design
    spec docs/superpowers/specs/2026-08-27-revenue-manager-scan-design.md
    §7.1) — a Slack file can be "sent to Scanner" or "sent to Revenue" from
    the same underlying bridge, distinguished only by which URL it hits."""
    if not file_obj.downloaded or not file_obj.s3_key:
        raise ScannerBridgeError("This file has not finished downloading from Slack yet")

    download_manager = DownloadManager(settings)
    s3_object = download_manager.s3_client.get_object(Bucket=settings.s3_bucket_name, Key=file_obj.s3_key)
    content = s3_object["Body"].read()

    base = settings.invoice_service_url.rstrip("/")
    scan_url = f"{base}/api/v1/invoices/scan" if invoice_type == "purchase" else f"{base}/api/v1/invoices/sales/scan"

    # Minted rather than a bare X-Company-ID header: Invoice Service runs with
    # TRUST_COMPANY_HEADER=false in Docker, so the header alone is rejected 401.
    # See shared.auth.internal_service_headers for the full reasoning.
    headers = internal_service_headers(
        secret_key=settings.jwt_secret_key, company_id=company_id, service_name="slack-connector",
    )

    try:
        # Generous: the scan this triggers runs OCR, which on a CPU-constrained
        # host genuinely takes minutes (measured — see docs/invoice-ocr-plan.md).
        # The previous 30s cut off scans that would otherwise have succeeded.
        async with httpx.AsyncClient(timeout=settings.invoice_service_timeout_seconds) as client:
            response = await client.post(
                scan_url,
                files={"file": (file_obj.filename, content, file_obj.mimetype or "application/octet-stream")},
                # The sales-scan endpoint is sale-only and takes no `type`
                # form field at all — only the purchase scanner needs it,
                # to pick between purchase and its own rejected "sale".
                data={"type": "purchase"} if invoice_type == "purchase" else {},
                headers=headers,
            )
    except httpx.TimeoutException as exc:
        raise ScannerBridgeError(
            "The AI Scanner did not respond in time. The file is still saved — try sending it again."
        ) from exc
    except httpx.RequestError as exc:
        # Invoice Service is not built yet, so this is the path most sends take
        # today. Without it the httpx error escaped uncaught and the caller got an
        # opaque 500 — which a bulk send turns into twenty opaque 500s.
        raise ScannerBridgeError(
            f"Could not reach the AI Scanner at {scan_url}. "
            "Check the Invoice Service is running."
        ) from exc

    if response.status_code >= 400:
        raise ScannerBridgeError(f"Invoice Service rejected the file: {response.status_code} {response.text}")

    try:
        return response.json()
    except ValueError as exc:
        raise ScannerBridgeError(
            "The AI Scanner returned a response that was not valid JSON."
        ) from exc
