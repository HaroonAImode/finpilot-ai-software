"""Books a purchase invoice as an Expense in Transactions Service, on
send-to-accounting — the real hand-off `invoices.py::send_to_accounting`'s
own docstring has flagged as "once Transactions Service exists" since
before that service was built.

Best-effort, matching `ai_engine_client.py`'s own reasoning: no queue exists
in this codebase yet (architecture report §11b), so every internal hand-off
is a plain HTTP call that degrades on failure rather than assuming
infrastructure that isn't there. A failure here is logged loudly but does
**not** block the status flip — an accountant clicking "Send to Accounting"
should not be stuck because a downstream service hiccuped. The real cost of
that choice: if Transactions Service is unreachable at that moment, no
Expense record is created and nothing retries it later. That gap is
explicitly documented (see the design spec's implementation notes), not
silently accepted — it closes once an outbox or a real queue exists.
"""
import logging
from typing import Optional
from uuid import UUID

import httpx
from shared.auth import internal_service_headers

from app.core.config import Settings

logger = logging.getLogger(__name__)


class TransactionsServiceError(Exception):
    """Transactions Service could not be reached or returned an error."""


async def book_expense_from_invoice(
    settings: Settings,
    *,
    company_id: UUID,
    invoice_id: UUID,
    vendor_id: Optional[UUID],
    vendor_name: Optional[str],
    invoice_number: Optional[str],
    invoice_date,
    amount: float,
    payment_method: Optional[str],
) -> None:
    url = f"{settings.transactions_service_url.rstrip('/')}/api/v1/expenses/book-from-invoice"
    payload = {
        "invoice_id": str(invoice_id),
        "vendor_id": str(vendor_id) if vendor_id else None,
        "vendor_name": vendor_name,
        "reference_id": invoice_number,
        "date": invoice_date.isoformat() if invoice_date else None,
        "amount_pkr": amount,
        # Transactions Service's PaymentMethod is a richer set than this
        # service's own bank/cash — mapped there, not here, so this client
        # stays a thin proxy rather than owning another service's enum.
        "payment_method": payment_method,
    }
    try:
        token = internal_service_headers(
            secret_key=settings.jwt_secret_key, company_id=company_id, service_name="invoice-service",
        )
    except ValueError as exc:
        raise TransactionsServiceError(str(exc)) from exc

    try:
        async with httpx.AsyncClient(timeout=settings.transactions_service_timeout_seconds) as client:
            response = await client.post(url, json=payload, headers=token)
    except httpx.TimeoutException as exc:
        raise TransactionsServiceError("Transactions Service did not respond in time") from exc
    except httpx.RequestError as exc:
        raise TransactionsServiceError(f"Could not reach Transactions Service at {url}") from exc

    if response.status_code >= 400:
        logger.error("Transactions Service returned %s for book-from-invoice: %s", response.status_code, response.text)
        raise TransactionsServiceError(f"Transactions Service returned an unexpected error ({response.status_code})")
