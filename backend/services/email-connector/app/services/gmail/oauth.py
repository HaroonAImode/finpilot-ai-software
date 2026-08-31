"""Google OAuth 2.0 — authorization, token exchange, and refresh.

gmail.readonly is the narrowest scope that lets the sync orchestrator (next
phase) list messages and fetch attachments; openid+email are added only to
resolve which address was connected, via the userinfo endpoint below, not to
read profile data beyond that. See docs/email-connector-plan.md §9 on scope
minimalism.
"""
from urllib.parse import urlencode

import httpx

from app.core.config import Settings

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

SCOPES = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "openid",
    "email",
)


def authorization_url(settings: Settings, state: str) -> str:
    query = urlencode(
        {
            "client_id": settings.google_client_id,
            "redirect_uri": settings.google_redirect_uri,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "state": state,
            # offline + consent guarantees a refresh_token comes back. Without
            # prompt=consent, Google only issues one on a user's *first ever*
            # authorization — a reconnect after revoking access would silently
            # get no refresh_token and the account would work for one hour
            # then die with no way to recover short of the user deleting
            # FinPilot's access from their Google account first.
            "access_type": "offline",
            "prompt": "consent",
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


async def exchange_code(settings: Settings, code: str) -> dict:
    """Trade an authorization code for tokens. Raises on any non-2xx response."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            TOKEN_URL,
            data={
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "code": code,
                "redirect_uri": settings.google_redirect_uri,
                "grant_type": "authorization_code",
            },
        )
    response.raise_for_status()
    return response.json()


async def refresh_access_token(settings: Settings, refresh_token: str) -> dict:
    """Exchange a refresh token for a new access token.

    Google does not return a new refresh_token here — the original one keeps
    working until the user revokes it. Callers must not overwrite the stored
    refresh_token_encrypted with this response's (missing) value.
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            TOKEN_URL,
            data={
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
    response.raise_for_status()
    return response.json()


async def fetch_email_address(access_token: str) -> str:
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}
        )
    response.raise_for_status()
    payload = response.json()
    email = payload.get("email")
    if not email:
        raise ValueError("Google userinfo response did not include an email address")
    return email
