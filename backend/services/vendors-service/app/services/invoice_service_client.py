"""Reads invoice data belonging to a vendor, from Invoice Service.

Vendors and invoices live in separate services with separate databases (the
architecture report's §5.3/§5.7 split), so `total_spend_pkr` and
"this vendor's invoices" cannot be a SQL join. That is a real cost of the
chosen boundary, accepted deliberately — and mitigated by asking for spend
**in bulk**: one aggregate call for every vendor, rather than one call per
vendor, which would be an N+1 across a network hop.

Authenticated with `shared.auth.internal_service_headers` — a bare
`X-Company-ID` gets a flat 401 from Invoice Service, which runs with
`TRUST_COMPANY_HEADER=false`. See that helper for the full reasoning.
"""
import logging
from uuid import UUID

import httpx
from shared.auth import internal_service_headers

from app.core.config import Settings

logger = logging.getLogger(__name__)


class InvoiceServiceError(Exception):
    """Invoice Service could not be reached or returned an error. Callers
    decide whether that is fatal (listing a vendor's invoices) or merely
    degraded (spend figures on the vendor list)."""


def _headers(settings: Settings, company_id: UUID) -> dict[str, str]:
    try:
        return internal_service_headers(
            secret_key=settings.jwt_secret_key, company_id=company_id, service_name="vendors-service",
        )
    except ValueError as exc:
        raise InvoiceServiceError(str(exc)) from exc


async def fetch_vendor_spend(settings: Settings, company_id: UUID) -> dict[UUID, tuple[float, int]]:
    """Total spend and invoice count per vendor, for the whole company.

    Returns `{vendor_id: (total_spend, invoice_count)}` — one call covering
    every vendor, so the list endpoint stays a single round trip regardless
    of how many vendors there are.
    """
    url = f"{settings.invoice_service_url.rstrip('/')}/api/v1/invoices/vendor-spend"
    try:
        async with httpx.AsyncClient(timeout=settings.invoice_service_timeout_seconds) as client:
            response = await client.get(url, headers=_headers(settings, company_id))
    except httpx.TimeoutException as exc:
        raise InvoiceServiceError("Invoice Service did not respond in time") from exc
    except httpx.RequestError as exc:
        raise InvoiceServiceError(f"Could not reach Invoice Service at {url}") from exc

    if response.status_code >= 400:
        logger.error("Invoice Service returned %s for vendor spend: %s", response.status_code, response.text)
        raise InvoiceServiceError(f"Invoice Service returned an unexpected error ({response.status_code})")

    return {
        UUID(row["vendor_id"]): (float(row.get("total_spend") or 0.0), int(row.get("invoice_count") or 0))
        for row in response.json()
    }


async def fetch_vendor_groups(settings: Settings, company_id: UUID) -> list[dict]:
    """Every distinct raw vendor_name on an unlinked purchase invoice, one
    entry per name with the invoice ids that carry it.

    Raised rather than degraded on failure: unlike spend, the whole
    reconciliation queue *is* this downstream data — returning an empty
    queue would tell the accountant there is nothing left to reconcile,
    which is a claim about reality we cannot make when we simply could not
    ask. Same reasoning as fetch_vendor_invoices below.
    """
    url = f"{settings.invoice_service_url.rstrip('/')}/api/v1/invoices/vendor-groups"
    try:
        async with httpx.AsyncClient(timeout=settings.invoice_service_timeout_seconds) as client:
            response = await client.get(url, headers=_headers(settings, company_id))
    except httpx.TimeoutException as exc:
        raise InvoiceServiceError("Invoice Service did not respond in time") from exc
    except httpx.RequestError as exc:
        raise InvoiceServiceError(f"Could not reach Invoice Service at {url}") from exc

    if response.status_code >= 400:
        logger.error("Invoice Service returned %s for vendor groups: %s", response.status_code, response.text)
        raise InvoiceServiceError(f"Invoice Service returned an unexpected error ({response.status_code})")

    return response.json()


async def bulk_link_vendor(
    settings: Settings, company_id: UUID, invoice_ids: list[UUID], vendor_id: UUID,
) -> int:
    """Sets vendor_id on every listed invoice in one call. Returns how many
    were actually updated — Invoice Service silently drops ids that no
    longer match this company, so the caller can tell a full batch from a
    partial one."""
    url = f"{settings.invoice_service_url.rstrip('/')}/api/v1/invoices/bulk-link-vendor"
    payload = {"invoice_ids": [str(i) for i in invoice_ids], "vendor_id": str(vendor_id)}
    try:
        async with httpx.AsyncClient(timeout=settings.invoice_service_timeout_seconds) as client:
            response = await client.post(url, json=payload, headers=_headers(settings, company_id))
    except httpx.TimeoutException as exc:
        raise InvoiceServiceError("Invoice Service did not respond in time") from exc
    except httpx.RequestError as exc:
        raise InvoiceServiceError(f"Could not reach Invoice Service at {url}") from exc

    if response.status_code >= 400:
        logger.error("Invoice Service returned %s for bulk-link: %s", response.status_code, response.text)
        raise InvoiceServiceError(f"Invoice Service returned an unexpected error ({response.status_code})")

    return int(response.json().get("updated_count") or 0)


async def fetch_vendor_invoices(
    settings: Settings, company_id: UUID, vendor_id: UUID, *, skip: int, limit: int,
) -> dict:
    """This vendor's invoices, proxied straight through.

    Returned as Invoice Service's own list shape rather than remapped: the
    frontend already has a type for it, and translating it here would mean
    two places to update whenever that contract changes.
    """
    url = f"{settings.invoice_service_url.rstrip('/')}/api/v1/invoices/"
    try:
        async with httpx.AsyncClient(timeout=settings.invoice_service_timeout_seconds) as client:
            response = await client.get(
                url,
                params={"vendor_id": str(vendor_id), "skip": skip, "limit": limit, "type": "purchase"},
                headers=_headers(settings, company_id),
            )
    except httpx.TimeoutException as exc:
        raise InvoiceServiceError("Invoice Service did not respond in time") from exc
    except httpx.RequestError as exc:
        raise InvoiceServiceError(f"Could not reach Invoice Service at {url}") from exc

    if response.status_code >= 400:
        logger.error("Invoice Service returned %s for vendor invoices: %s", response.status_code, response.text)
        raise InvoiceServiceError(f"Invoice Service returned an unexpected error ({response.status_code})")

    return response.json()
