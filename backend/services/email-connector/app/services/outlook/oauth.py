"""Microsoft identity platform OAuth 2.0 — authorization, token exchange,
and refresh, for connecting an Outlook/Microsoft 365 mailbox via Graph.
Mirrors gmail/oauth.py's shape deliberately (plan §11a) so both providers
plug into the same auth.py routes with one small branch, not two rewrites.

Not yet live-verified against a real Microsoft account: doing so needs an
Azure AD App Registration (client id/secret), which only the account owner
can create — see docs/email-connector-plan.md §11a for what's needed before
this can be exercised end-to-end. Every function here is unit-tested against
mocked HTTP the same way gmail/oauth.py's equivalents are.
"""
from urllib.parse import urlencode

import httpx

from app.core.config import Settings, ConfigurationError

# "common" accepts both work/school (Azure AD) accounts and personal
# Microsoft accounts (outlook.com/hotmail.com/live.com) — the standard
# tenant for a multi-tenant SaaS app that does not know in advance which
# kind of account a given customer will connect.
AUTHORIZE_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
GRAPH_ME_URL = "https://graph.microsoft.com/v1.0/me"

# offline_access is what earns a refresh_token back — without it Microsoft
# only ever issues an access token, the same reasoning as Gmail's
# access_type=offline. Mail.Read is read-only, no send/modify capability,
# matching gmail.readonly's scope-minimalism (plan §9).
SCOPES = ("openid", "email", "offline_access", "https://graph.microsoft.com/Mail.Read")


def _require_configured(settings: Settings) -> None:
    if not settings.microsoft_client_id or not settings.microsoft_client_secret or not settings.microsoft_redirect_uri:
        raise ConfigurationError(
            "Outlook is not configured on this deployment — MICROSOFT_CLIENT_ID, "
            "MICROSOFT_CLIENT_SECRET and MICROSOFT_REDIRECT_URI must all be set "
            "(an Azure AD App Registration is required; see "
            "docs/email-connector-plan.md §11a)."
        )


def authorization_url(settings: Settings, state: str) -> str:
    _require_configured(settings)
    query = urlencode(
        {
            "client_id": settings.microsoft_client_id,
            "redirect_uri": settings.microsoft_redirect_uri,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "state": state,
            "response_mode": "query",
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


async def exchange_code(settings: Settings, code: str) -> dict:
    """Trade an authorization code for tokens. Raises on any non-2xx response."""
    _require_configured(settings)
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            TOKEN_URL,
            data={
                "client_id": settings.microsoft_client_id,
                "client_secret": settings.microsoft_client_secret,
                "code": code,
                "redirect_uri": settings.microsoft_redirect_uri,
                "grant_type": "authorization_code",
                "scope": " ".join(SCOPES),
            },
        )
    response.raise_for_status()
    return response.json()


async def refresh_access_token(settings: Settings, refresh_token: str) -> dict:
    """Exchange a refresh token for a new access token.

    Unlike Google, Microsoft (almost) always rotates the refresh_token on
    every use — providers/token_manager.py already persists whichever one
    comes back in the response (or keeps the old one if none did), so this
    asymmetry is handled once, generically, rather than here.
    """
    _require_configured(settings)
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            TOKEN_URL,
            data={
                "client_id": settings.microsoft_client_id,
                "client_secret": settings.microsoft_client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
                "scope": " ".join(SCOPES),
            },
        )
    response.raise_for_status()
    return response.json()


async def fetch_email_address(access_token: str) -> str:
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            GRAPH_ME_URL,
            params={"$select": "mail,userPrincipalName"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
    response.raise_for_status()
    payload = response.json()
    # `mail` is unset for some personal/consumer accounts — userPrincipalName
    # is the documented fallback (commonly the same value as the sign-in
    # address in that case).
    email = payload.get("mail") or payload.get("userPrincipalName")
    if not email:
        raise ValueError("Microsoft Graph's /me response did not include an email address")
    return email
