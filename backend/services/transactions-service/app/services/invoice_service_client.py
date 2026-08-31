"""Reads revenue and invoice-count data from Invoice Service.

This service owns Expenses only. Revenue stays Invoice Service's own
scanned-sales ledger (Revenue Manager) — see the design spec's scope note
for why a second Revenue table here was deliberately rejected. The
Dashboard's revenue-side KPIs and the revenue half of both trend charts are
read from there on every request, the same "derive rather than duplicate"
reasoning vendors-service already applies to vendor spend.

Authenticated with `shared.auth.internal_service_headers` — a bare
`X-Company-ID` gets a flat 401 from Invoice Service, which runs with
`TRUST_COMPANY_HEADER=false`. See that helper's own docstring for why.
"""
import logging
from uuid import UUID

import httpx
from shared.auth import internal_service_headers

from app.core.config import Settings

logger = logging.getLogger(__name__)


class InvoiceServiceError(Exception):
    """Invoice Service could not be reached or returned an error. Callers
    degrade the revenue-derived figures rather than failing the whole
    dashboard — expenses are this service's own data and stay correct
    either way."""


def _headers(settings: Settings, company_id: UUID) -> dict[str, str]:
    try:
        return internal_service_headers(
            secret_key=settings.jwt_secret_key, company_id=company_id, service_name="transactions-service",
        )
    except ValueError as exc:
        raise InvoiceServiceError(str(exc)) from exc


async def fetch_sales_summary(settings: Settings, company_id: UUID) -> dict:
    """Revenue Manager's own KPI/chart aggregate, proxied straight through —
    see invoice-service/app/schemas/sales_summary.py for its shape."""
    url = f"{settings.invoice_service_url.rstrip('/')}/api/v1/invoices/sales/summary"
    try:
        async with httpx.AsyncClient(timeout=settings.invoice_service_timeout_seconds) as client:
            response = await client.get(url, headers=_headers(settings, company_id))
    except httpx.TimeoutException as exc:
        raise InvoiceServiceError("Invoice Service did not respond in time") from exc
    except httpx.RequestError as exc:
        raise InvoiceServiceError(f"Could not reach Invoice Service at {url}") from exc

    if response.status_code >= 400:
        logger.error("Invoice Service returned %s for sales summary: %s", response.status_code, response.text)
        raise InvoiceServiceError(f"Invoice Service returned an unexpected error ({response.status_code})")
    return response.json()


async def fetch_invoice_status_counts(settings: Settings, company_id: UUID) -> dict:
    """Purchase invoice status counts, for the Pending/Processed Invoices KPIs."""
    url = f"{settings.invoice_service_url.rstrip('/')}/api/v1/invoices/status-summary"
    try:
        async with httpx.AsyncClient(timeout=settings.invoice_service_timeout_seconds) as client:
            response = await client.get(url, headers=_headers(settings, company_id))
    except httpx.TimeoutException as exc:
        raise InvoiceServiceError("Invoice Service did not respond in time") from exc
    except httpx.RequestError as exc:
        raise InvoiceServiceError(f"Could not reach Invoice Service at {url}") from exc

    if response.status_code >= 400:
        logger.error("Invoice Service returned %s for status summary: %s", response.status_code, response.text)
        raise InvoiceServiceError(f"Invoice Service returned an unexpected error ({response.status_code})")
    return response.json()
