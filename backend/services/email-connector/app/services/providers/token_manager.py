"""Keeps an EmailAccount's access token usable across a sync run.

Provider-generic since Phase 5 (plan §11a) — originally lived at
app/services/gmail/token_manager.py when Gmail was the only provider;
moved here once Outlook needed the exact same reactive-refresh behaviour.
Pulled forward from the plan's Phase 4 originally because it isn't actually
optional for a first sync: an access token lasts about an hour, a mailbox
sync can easily take longer, and even a short sync started an hour after
connecting would otherwise fail outright.
"""
from datetime import datetime, timedelta, timezone

from app.core.config import Settings
from app.core.security import TokenCipher
from app.models import EmailAccount, EmailAccountStatus, EmailProviderName
from app.services.gmail import oauth as gmail_oauth
from app.services.outlook import oauth as outlook_oauth

# Refresh a bit before actual expiry so a long-running sync never has a
# token die mid-call — same margin the plan doc calls for in §8.
REFRESH_MARGIN = timedelta(minutes=5)

_OAUTH_MODULES = {
    EmailProviderName.gmail: gmail_oauth,
    EmailProviderName.outlook: outlook_oauth,
}


class TokenRefreshError(Exception):
    """Raised when the stored refresh token no longer works — the account
    needs the user to reconnect, not just retry."""


async def get_valid_access_token(account: EmailAccount, settings: Settings) -> str:
    """Returns a usable access token for `account`, refreshing and
    persisting a new one first if the current one is expiring soon.

    Caller is responsible for committing `account`'s changes — this only
    mutates the passed-in ORM object, matching how the rest of this service
    keeps commits at the route/orchestrator boundary rather than scattered
    through helpers.
    """
    cipher = TokenCipher(settings.token_encryption_key)

    if account.token_expires_at - datetime.now(timezone.utc) > REFRESH_MARGIN:
        return cipher.decrypt(account.access_token_encrypted)

    oauth_module = _OAUTH_MODULES[account.provider]
    refresh_token = cipher.decrypt(account.refresh_token_encrypted)
    try:
        payload = await oauth_module.refresh_access_token(settings, refresh_token)
    except Exception as exc:
        # Revoked by the user, or the refresh token itself expired. Either
        # way retrying won't help — the account needs reconnecting, and the
        # UI's "Needs reconnecting" badge (see EmailConnectedAppsCard) is
        # what surfaces that rather than the sync just failing silently.
        account.status = EmailAccountStatus.needs_reauth
        raise TokenRefreshError(
            f"Could not refresh {account.provider.value} access token: {exc}"
        ) from exc

    new_access_token = payload.get("access_token")
    expires_in = payload.get("expires_in")
    if not new_access_token or not expires_in:
        account.status = EmailAccountStatus.needs_reauth
        raise TokenRefreshError(f"{account.provider.value}'s refresh response was incomplete")

    account.access_token_encrypted = cipher.encrypt(new_access_token)
    account.token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))

    # Google never rotates the refresh token on a refresh call (the original
    # keeps working); Microsoft (almost) always does. Handled with one check
    # rather than a per-provider branch: persist a new one only when the
    # provider actually sent one, otherwise the stored one is left alone.
    new_refresh_token = payload.get("refresh_token")
    if new_refresh_token:
        account.refresh_token_encrypted = cipher.encrypt(new_refresh_token)

    return new_access_token
