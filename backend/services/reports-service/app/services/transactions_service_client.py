"""Reads expense data from Transactions Service — the expense-side half
of every report except the purely-revenue ones (Sales Report).

A failure here is fatal (raises, turned into a 502 by the route) rather
than degraded — same reasoning as invoice_service_client.py.
"""
import logging
from datetime import date
from typing import Optional
from uuid import UUID

import httpx
from shared.auth import internal_service_headers

from app.core.config import Settings

logger = logging.getLogger(__name__)

#: Transactions Service's own list endpoint caps `limit` at 200 — a real
#: bound on one HTTP response, not a report-generation constraint. Pages
#: through it rather than requesting an impossible single "all" call.
_PAGE_SIZE = 200


class TransactionsServiceError(Exception):
    """Transactions Service could not be reached or returned an error."""


def _headers(settings: Settings, company_id: UUID) -> dict[str, str]:
    try:
        return internal_service_headers(
            secret_key=settings.jwt_secret_key, company_id=company_id, service_name="reports-service",
        )
    except ValueError as exc:
        raise TransactionsServiceError(str(exc)) from exc


async def _get(settings: Settings, company_id: UUID, path: str, params: dict) -> dict:
    url = f"{settings.transactions_service_url.rstrip('/')}{path}"
    try:
        async with httpx.AsyncClient(timeout=settings.upstream_timeout_seconds) as client:
            response = await client.get(url, params=params, headers=_headers(settings, company_id))
    except httpx.TimeoutException as exc:
        raise TransactionsServiceError("Transactions Service did not respond in time") from exc
    except httpx.RequestError as exc:
        raise TransactionsServiceError(f"Could not reach Transactions Service at {url}") from exc

    if response.status_code >= 400:
        logger.error("Transactions Service returned %s for %s: %s", response.status_code, path, response.text)
        raise TransactionsServiceError(f"Transactions Service returned an unexpected error ({response.status_code})")
    return response.json()


async def fetch_expense_summary(settings: Settings, company_id: UUID, *, date_from: date, date_to: date) -> dict:
    """The Total/Approved/Pending/Rejected breakdown, this period.

    Both bounds are always sent here (unlike the invoice-service clients'
    independently-optional ones): that endpoint falls back to "this
    calendar month" unless *both* `date_from` and `date_to` are given —
    an existing behaviour built for its own primary caller, not changed
    for this one. A report always has two real bounds anyway (even
    Balance Sheet passes a synthetic all-time start — see
    report_generator.py), so this is never a real limitation here.
    """
    return await _get(
        settings, company_id, "/api/v1/expenses/summary",
        {"date_from": date_from.isoformat(), "date_to": date_to.isoformat()},
    )


async def fetch_expense_categories(settings: Settings, company_id: UUID, *, date_from: date, date_to: date) -> list[dict]:
    return await _get(
        settings, company_id, "/api/v1/expenses/categories",
        {"date_from": date_from.isoformat(), "date_to": date_to.isoformat()},
    )


async def fetch_all_approved_expenses(
    settings: Settings, company_id: UUID, *, date_from: date, date_to: date,
) -> list[dict]:
    """Every approved expense in the period, paginated through — used for
    Cash Flow's month-by-month breakdown, which needs each expense's own
    date rather than a single period total."""
    expenses: list[dict] = []
    skip = 0
    while True:
        page = await _get(
            settings, company_id, "/api/v1/expenses/",
            {
                "status": "approved", "date_from": date_from.isoformat(), "date_to": date_to.isoformat(),
                "skip": str(skip), "limit": str(_PAGE_SIZE),
            },
        )
        expenses.extend(page["expenses"])
        if len(page["expenses"]) < _PAGE_SIZE:
            break
        skip += _PAGE_SIZE
    return expenses
