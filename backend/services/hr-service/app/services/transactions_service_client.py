"""Books a processed payroll run as an Expense in Transactions Service —
the same "real hand-off, best-effort" pattern Invoice Service's
send-to-accounting uses for a purchase invoice (see that service's own
transactions_service_client.py). No queue exists in this codebase yet
(architecture report §11b), so a failure here is logged and does not roll
back the payroll run itself: the employees were still paid as far as this
service's own PayrollRecord history is concerned, even if the accounting
side of it needs a manual follow-up.
"""
import logging
from uuid import UUID

import httpx
from shared.auth import internal_service_headers

from app.core.config import Settings

logger = logging.getLogger(__name__)


class TransactionsServiceError(Exception):
    """Transactions Service could not be reached or returned an error."""


async def book_payroll_expense(
    settings: Settings, *, company_id: UUID, period: str, amount: float, reference_id: str,
) -> None:
    url = f"{settings.transactions_service_url.rstrip('/')}/api/v1/expenses/book-from-payroll"
    payload = {
        "period": period,
        "amount_pkr": amount,
        "reference_id": reference_id,
        "vendor_name": f"Payroll — {period}",
    }
    try:
        headers = internal_service_headers(
            secret_key=settings.jwt_secret_key, company_id=company_id, service_name="hr-service",
        )
    except ValueError as exc:
        raise TransactionsServiceError(str(exc)) from exc

    try:
        async with httpx.AsyncClient(timeout=settings.transactions_service_timeout_seconds) as client:
            response = await client.post(url, json=payload, headers=headers)
    except httpx.TimeoutException as exc:
        raise TransactionsServiceError("Transactions Service did not respond in time") from exc
    except httpx.RequestError as exc:
        raise TransactionsServiceError(f"Could not reach Transactions Service at {url}") from exc

    if response.status_code >= 400:
        logger.error("Transactions Service returned %s for book-from-payroll: %s", response.status_code, response.text)
        raise TransactionsServiceError(f"Transactions Service returned an unexpected error ({response.status_code})")
