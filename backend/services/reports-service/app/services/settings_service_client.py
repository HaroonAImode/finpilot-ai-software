"""Reads tax configuration from Settings Service — Tax Summary's own rates
(GST, withholding, filer status), so a company's actual configured rates
are used instead of a hardcoded assumption baked into this service."""
import logging
from uuid import UUID

import httpx
from shared.auth import internal_service_headers

from app.core.config import Settings

logger = logging.getLogger(__name__)


class SettingsServiceError(Exception):
    """Settings Service could not be reached or returned an error."""


async def fetch_company_profile(settings: Settings, company_id: UUID) -> dict:
    url = f"{settings.settings_service_url.rstrip('/')}/api/v1/settings/company"
    try:
        headers = internal_service_headers(
            secret_key=settings.jwt_secret_key, company_id=company_id, service_name="reports-service",
        )
    except ValueError as exc:
        raise SettingsServiceError(str(exc)) from exc

    try:
        async with httpx.AsyncClient(timeout=settings.upstream_timeout_seconds) as client:
            response = await client.get(url, headers=headers)
    except httpx.TimeoutException as exc:
        raise SettingsServiceError("Settings Service did not respond in time") from exc
    except httpx.RequestError as exc:
        raise SettingsServiceError(f"Could not reach Settings Service at {url}") from exc

    if response.status_code >= 400:
        logger.error("Settings Service returned %s for company profile: %s", response.status_code, response.text)
        raise SettingsServiceError(f"Settings Service returned an unexpected error ({response.status_code})")
    return response.json()


async def fetch_tax_settings(settings: Settings, company_id: UUID) -> dict:
    url = f"{settings.settings_service_url.rstrip('/')}/api/v1/settings/tax"
    try:
        headers = internal_service_headers(
            secret_key=settings.jwt_secret_key, company_id=company_id, service_name="reports-service",
        )
    except ValueError as exc:
        raise SettingsServiceError(str(exc)) from exc

    try:
        async with httpx.AsyncClient(timeout=settings.upstream_timeout_seconds) as client:
            response = await client.get(url, headers=headers)
    except httpx.TimeoutException as exc:
        raise SettingsServiceError("Settings Service did not respond in time") from exc
    except httpx.RequestError as exc:
        raise SettingsServiceError(f"Could not reach Settings Service at {url}") from exc

    if response.status_code >= 400:
        logger.error("Settings Service returned %s for tax settings: %s", response.status_code, response.text)
        raise SettingsServiceError(f"Settings Service returned an unexpected error ({response.status_code})")
    return response.json()
