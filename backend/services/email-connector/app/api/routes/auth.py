import json
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import ConfigurationError, Settings, get_settings
from app.core.security import TokenCipher
from app.core.tenancy import get_company_id, get_optional_account_for_company
from app.db.session import get_db
from app.models import EmailAccount, EmailAccountStatus, EmailProviderName
from app.services.gmail.oauth import authorization_url, exchange_code, fetch_email_address
from app.services.outlook.oauth import (
    authorization_url as outlook_authorization_url,
    exchange_code as outlook_exchange_code,
    fetch_email_address as outlook_fetch_email_address,
)

router = APIRouter(tags=["auth"])


def _build_state(
    cipher: TokenCipher, company_id: UUID, provider: EmailProviderName = EmailProviderName.gmail,
) -> tuple[str, str]:
    nonce = secrets.token_urlsafe(32)
    payload = json.dumps({"nonce": nonce, "company_id": str(company_id), "provider": provider.value})
    return cipher.encrypt_state(payload), nonce


def _parse_state(cipher: TokenCipher, encrypted_state: str) -> tuple[UUID, str, EmailProviderName]:
    payload = cipher.decrypt_state(encrypted_state)
    data = json.loads(payload)
    # .get(..., default) rather than a required key: a state minted before
    # Phase 5 added multi-provider support would have no "provider" field.
    # Such a state can only be outstanding for a few minutes (bounded by the
    # nonce cookie's own max_age), so this is just "don't 500 an OAuth flow
    # that was already in flight when this shipped", not a real migration.
    provider = EmailProviderName(data.get("provider", EmailProviderName.gmail.value))
    return UUID(data["company_id"]), data["nonce"], provider


@router.get("/connect")
async def start_oauth(
    response: Response,
    provider: str = Query(default="gmail", description="Which mailbox provider to connect: 'gmail' or 'outlook'."),
    company_id: UUID = Depends(get_company_id),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Return the provider's consent URL for the caller to navigate to.

    JSON rather than a 302, for the same reason as Slack: this route is
    company-scoped and needs a bearer token, which a browser navigation
    cannot carry. The frontend fetches this with the token attached, then
    sets window.location to the returned URL.
    """
    try:
        provider_enum = EmailProviderName(provider)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown provider '{provider}'")

    cipher = TokenCipher(settings.token_encryption_key)
    state, nonce = _build_state(cipher, company_id, provider_enum)
    response.set_cookie(
        "email_oauth_nonce", nonce, max_age=600, httponly=True, samesite="lax",
        secure=settings.app_env != "development",
    )
    try:
        if provider_enum == EmailProviderName.outlook:
            return {"authorize_url": outlook_authorization_url(settings, state)}
        return {"authorize_url": authorization_url(settings, state)}
    except ConfigurationError as exc:
        # A missing Azure/Google app registration is an admin-facing setup
        # gap, not a caller error — surfaced as a clean 503 rather than an
        # unhandled 500, so the frontend can show "not available yet"
        # instead of a generic failure.
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/callback")
async def oauth_callback(
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    if error:
        raise HTTPException(status_code=400, detail="Authorization was not completed")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing OAuth callback parameters")

    cipher = TokenCipher(settings.token_encryption_key)
    try:
        company_id, expected_nonce, provider = _parse_state(cipher, state)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state") from exc
    if not secrets.compare_digest(expected_nonce, request.cookies.get("email_oauth_nonce", "")):
        raise HTTPException(status_code=400, detail="Invalid OAuth state")

    is_outlook = provider == EmailProviderName.outlook
    provider_label = provider.value.title()

    try:
        payload = await (outlook_exchange_code(settings, code) if is_outlook else exchange_code(settings, code))
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"{provider_label} OAuth token exchange failed") from exc
    except ConfigurationError as exc:
        # Config changed (or was never set) between /connect issuing this
        # state and the provider calling back — same admin-facing gap as
        # /connect's, surfaced the same way rather than an unhandled 500.
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    access_token = payload.get("access_token")
    refresh_token = payload.get("refresh_token")
    expires_in = payload.get("expires_in")
    if not access_token or not expires_in:
        raise HTTPException(status_code=502, detail=f"{provider_label} OAuth response was incomplete")
    if not refresh_token:
        # Google omits this when the user already granted consent before
        # without prompt=consent forcing a fresh one; Outlook's
        # offline_access scope carries the same requirement. Both
        # authorization_url functions always request the equivalent of a
        # fresh consent, so this should not happen in practice — but if it
        # does, silently storing no refresh token would mean the account
        # dies in an hour with nothing to recover it, so fail loudly instead.
        raise HTTPException(
            status_code=502,
            detail=f"{provider_label} did not return a refresh token. Disconnect any prior FinPilot "
            f"access in your {provider_label} account settings and try connecting again.",
        )

    try:
        email_address = await (
            outlook_fetch_email_address(access_token) if is_outlook else fetch_email_address(access_token)
        )
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(
            status_code=502, detail=f"Could not resolve the connected {provider_label} account's email address"
        ) from exc

    scopes = (payload.get("scope") or "").split()
    token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))

    other_owner = (
        await db.execute(select(EmailAccount).where(EmailAccount.email_address == email_address))
    ).scalar_one_or_none()
    if other_owner and other_owner.company_id != company_id:
        raise HTTPException(
            status_code=409,
            detail=f"This {provider_label} account is already connected to a different FinPilot company",
        )

    cipher = TokenCipher(settings.token_encryption_key)
    account = (
        await db.execute(select(EmailAccount).where(EmailAccount.company_id == company_id))
    ).scalar_one_or_none()
    if account is None:
        account = EmailAccount(
            company_id=company_id,
            provider=provider,
            email_address=email_address,
            access_token_encrypted=cipher.encrypt(access_token),
            refresh_token_encrypted=cipher.encrypt(refresh_token),
            token_expires_at=token_expires_at,
            scopes=scopes,
            status=EmailAccountStatus.active,
        )
        db.add(account)
    else:
        account.provider = provider
        account.email_address = email_address
        account.access_token_encrypted = cipher.encrypt(access_token)
        account.refresh_token_encrypted = cipher.encrypt(refresh_token)
        account.token_expires_at = token_expires_at
        account.scopes = scopes
        account.status = EmailAccountStatus.active
        # A reconnect can switch provider (disconnect Gmail, connect
        # Outlook, or vice versa) — a cursor from the old provider means
        # nothing to the new one and must never be reused as if valid.
        account.sync_cursor = None
    await db.commit()

    target = f"{settings.frontend_base_url.rstrip('/')}/app/settings?{urlencode({'tab': 'connected-apps', 'email': email_address})}"
    response = RedirectResponse(target, status_code=302)
    response.delete_cookie("email_oauth_nonce")
    return response


@router.get("/status")
async def get_connection_status(
    account: EmailAccount | None = Depends(get_optional_account_for_company),
) -> dict:
    if account is None:
        return {"connected": False}
    return {
        "connected": account.status == EmailAccountStatus.active,
        "provider": account.provider.value,
        "email_address": account.email_address,
        "scopes": account.scopes,
        "connected_at": account.connected_at.isoformat(),
        "last_synced_at": account.last_synced_at.isoformat() if account.last_synced_at else None,
        "needs_reauth": account.status == EmailAccountStatus.needs_reauth,
    }


@router.delete("/account")
async def disconnect_email_account(
    account: EmailAccount | None = Depends(get_optional_account_for_company),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Revoke and forget the connection.

    A mailbox holds personal, financial and legal correspondence in one
    place — more sensitive than a Slack workspace — so per
    docs/email-connector-plan.md §9, disconnecting hard-deletes the row
    (encrypted tokens included) rather than flipping a status flag the way
    Slack's disconnect does. EmailMessage/EmailAttachment foreign keys
    cascade from this table, so a disconnect also removes anything already
    downloaded.
    """
    if account is None:
        raise HTTPException(status_code=404, detail="No email account is connected for this company")
    await db.delete(account)
    await db.commit()
    return {"connected": False}
