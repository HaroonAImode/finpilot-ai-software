from urllib.parse import urlencode

import httpx

from app.core.config import Settings

BOT_SCOPES = (
    "channels:read",
    "channels:history",
    "groups:read",
    "groups:history",
    "im:read",
    "im:history",
    "mpim:read",
    "mpim:history",
    "files:read",
    "users:read",
)


def authorization_url(settings: Settings, state: str) -> str:
    query = urlencode({"client_id": settings.slack_client_id, "scope": ",".join(BOT_SCOPES), "redirect_uri": settings.slack_redirect_uri, "state": state})
    return f"https://slack.com/oauth/v2/authorize?{query}"


async def exchange_code(settings: Settings, code: str) -> dict:
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            "https://slack.com/api/oauth.v2.access",
            data={"client_id": settings.slack_client_id, "client_secret": settings.slack_client_secret, "code": code, "redirect_uri": settings.slack_redirect_uri},
        )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise ValueError(payload.get("error", "oauth_failed"))
    return payload
