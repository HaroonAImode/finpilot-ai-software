"""Reads revenue (Invoice Service's own scanned-sales ledger) and purchase
vendor-spend, both period-scoped — see that service's own `date_from`/
`date_to` additions to `/invoices/sales/summary` and `/invoices/vendor-
spend`, added specifically for this service.

A failure here is fatal (raises, turned into a 502 by the route) rather
than degraded: unlike a supplementary figure elsewhere in this codebase,
this response *is* the report being generated.
"""
import logging
from datetime import date
from typing import Optional
from uuid import UUID

import httpx
from shared.auth import internal_service_headers

from app.core.config import Settings

logger = logging.getLogger(__name__)


class InvoiceServiceError(Exception):
    """Invoice Service could not be reached or returned an error."""


def _headers(settings: Settings, company_id: UUID) -> dict[str, str]:
    try:
        return internal_service_headers(
            secret_key=settings.jwt_secret_key, company_id=company_id, service_name="reports-service",
        )
    except ValueError as exc:
        raise InvoiceServiceError(str(exc)) from exc


async def fetch_sales_summary(
    settings: Settings, company_id: UUID, *,
    date_from: Optional[date] = None, date_to: Optional[date] = None, customer_limit: int = 5,
) -> dict:
    url = f"{settings.invoice_service_url.rstrip('/')}/api/v1/invoices/sales/summary"
    params: dict[str, str] = {"customer_limit": str(customer_limit)}
    if date_from is not None:
        params["date_from"] = date_from.isoformat()
    if date_to is not None:
        params["date_to"] = date_to.isoformat()

    try:
        async with httpx.AsyncClient(timeout=settings.upstream_timeout_seconds) as client:
            response = await client.get(url, params=params, headers=_headers(settings, company_id))
    except httpx.TimeoutException as exc:
        raise InvoiceServiceError("Invoice Service did not respond in time") from exc
    except httpx.RequestError as exc:
        raise InvoiceServiceError(f"Could not reach Invoice Service at {url}") from exc

    if response.status_code >= 400:
        logger.error("Invoice Service returned %s for sales summary: %s", response.status_code, response.text)
        raise InvoiceServiceError(f"Invoice Service returned an unexpected error ({response.status_code})")
    return response.json()


async def fetch_vendor_spend(
    settings: Settings, company_id: UUID, *, date_from: Optional[date] = None, date_to: Optional[date] = None,
) -> list[dict]:
    url = f"{settings.invoice_service_url.rstrip('/')}/api/v1/invoices/vendor-spend"
    params: dict[str, str] = {}
    if date_from is not None:
        params["date_from"] = date_from.isoformat()
    if date_to is not None:
        params["date_to"] = date_to.isoformat()

    try:
        async with httpx.AsyncClient(timeout=settings.upstream_timeout_seconds) as client:
            response = await client.get(url, params=params, headers=_headers(settings, company_id))
    except httpx.TimeoutException as exc:
        raise InvoiceServiceError("Invoice Service did not respond in time") from exc
    except httpx.RequestError as exc:
        raise InvoiceServiceError(f"Could not reach Invoice Service at {url}") from exc

    if response.status_code >= 400:
        logger.error("Invoice Service returned %s for vendor spend: %s", response.status_code, response.text)
        raise InvoiceServiceError(f"Invoice Service returned an unexpected error ({response.status_code})")
    return response.json()
