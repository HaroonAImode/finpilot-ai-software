"""Hands a stored document to Invoice Service's scanner.

A plain internal HTTP POST, not a queue publish — the same choice, for the
same reason, as the Slack and Email connectors' own `scanner_bridge.py` and
Invoice Service's `ai_engine_client.py`: RabbitMQ does not exist in this
compose (see the architecture report's status note), so every hand-off in
this codebase degrades to a clear error instead of assuming infrastructure
that isn't there.
"""
import logging
import uuid
from typing import Optional
from uuid import UUID

import httpx
from shared.auth import create_access_token

from app.core.config import Settings

logger = logging.getLogger(__name__)

#: Short-lived: this token is minted, used for one internal call, and
#: discarded. It never reaches a browser and is never stored.
_INTERNAL_TOKEN_MINUTES = 5


def _internal_auth_header(settings: Settings, company_id: UUID) -> dict[str, str]:
    """Mints a short-lived token identifying the calling *service*.

    Why not just send `X-Company-ID`: Invoice Service runs with
    `TRUST_COMPANY_HEADER=false` in Docker (correctly — behind the Gateway
    that header is written from verified JWT claims, so trusting a raw one
    would let any caller name any company). A server-to-server call has no
    end-user Authorization header to forward, so sending only the header
    gets a flat 401 — confirmed live, and the same latent problem exists in
    the Slack and Email connectors' own scanner bridges.

    Every service already shares `JWT_SECRET_KEY`, so the honest fix is to
    mint a real token rather than ask the callee to trust an unverified
    header. `sub` is a fixed, non-user UUID so an audit trail can tell a
    service-initiated scan from a person-initiated one.
    """
    if not settings.jwt_secret_key:
        raise ScannerBridgeError(
            "JWT_SECRET_KEY is not configured, so this service cannot authenticate "
            "its call to Invoice Service."
        )
    token, _ = create_access_token(
        secret_key=settings.jwt_secret_key,
        user_id=uuid.UUID("00000000-0000-0000-0000-000000000000"),
        company_id=company_id,
        role="service",
        email="documents-service@internal",
        expires_minutes=_INTERNAL_TOKEN_MINUTES,
    )
    return {"Authorization": f"Bearer {token}", "X-Company-ID": str(company_id)}


class ScannerBridgeError(Exception):
    """Invoice Service could not be reached, timed out, or rejected the
    file. Callers record this on the document as `scanner_status=failed`
    rather than letting an httpx exception escape as an opaque 500."""


async def send_to_scanner(
    settings: Settings, *, filename: str, content: bytes, mimetype: Optional[str], company_id: UUID,
) -> Optional[UUID]:
    """POSTs the document to Invoice Service's `/invoices/scan`.

    Returns the created invoice's id, or None when the scan succeeded but
    returned no invoice id (a shape change on that side rather than a
    failure here — worth surfacing, not worth crashing on).

    Authenticated with a short-lived minted token rather than a bare
    tenant header — see `_internal_auth_header` for why the header alone
    is not enough.
    """
    url = f"{settings.invoice_service_url.rstrip('/')}/api/v1/invoices/scan"
    headers = _internal_auth_header(settings, company_id)

    try:
        async with httpx.AsyncClient(timeout=settings.invoice_service_timeout_seconds) as client:
            response = await client.post(
                url,
                files={"file": (filename, content, mimetype or "application/octet-stream")},
                data={"type": "purchase"},
                headers=headers,
            )
    except httpx.TimeoutException as exc:
        raise ScannerBridgeError(
            "The scanner did not respond in time. The document is saved — try sending it again."
        ) from exc
    except httpx.RequestError as exc:
        raise ScannerBridgeError(
            f"Could not reach Invoice Service at {url}. The document is saved; check the service is running."
        ) from exc

    if response.status_code == 400:
        detail = response.json().get("detail", "The scanner rejected this file")
        raise ScannerBridgeError(detail)
    if response.status_code >= 400:
        logger.error("Invoice Service returned %s: %s", response.status_code, response.text)
        raise ScannerBridgeError(f"The scanner returned an unexpected error ({response.status_code})")

    body = response.json()
    invoice_id = body.get("invoice_id")
    return UUID(invoice_id) if invoice_id else None
