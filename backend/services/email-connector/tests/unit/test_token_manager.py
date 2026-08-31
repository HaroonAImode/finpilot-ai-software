"""Access-token refresh — pulled forward from the plan's Phase 4 because a
sync literally cannot run without it (docs/email-connector-plan.md §8):
Gmail access tokens last about an hour, Slack's never expire, so this is
new logic with no Slack equivalent and no prior test to copy the shape of.

Moved to app/services/providers/token_manager.py in Phase 5 (plan §11a) once
Outlook needed the exact same reactive-refresh behaviour — patches now target
each provider's own oauth module (gmail.oauth / outlook.oauth) rather than a
name inside token_manager's own namespace, since token_manager no longer
imports either provider's refresh function directly.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.core.security import TokenCipher
from app.models import EmailAccount, EmailAccountStatus, EmailProviderName
from app.services.gmail import oauth as gmail_oauth
from app.services.outlook import oauth as outlook_oauth
from app.services.providers import token_manager

ENCRYPTION_KEY = "dGVzdC1mZXJuZXQta2V5LW11c3QtYmUtMzItYnl0ZXM="


def _account(*, expires_in: timedelta, access="old-access", refresh="stored-refresh", provider=EmailProviderName.gmail) -> EmailAccount:
    cipher = TokenCipher(ENCRYPTION_KEY)
    return EmailAccount(
        provider=provider, email_address="a@b.com",
        access_token_encrypted=cipher.encrypt(access),
        refresh_token_encrypted=cipher.encrypt(refresh),
        token_expires_at=datetime.now(timezone.utc) + expires_in,
        scopes=[], status=EmailAccountStatus.active,
    )


def _settings():
    from app.core.config import Settings

    return Settings(
        GOOGLE_CLIENT_ID="id", GOOGLE_CLIENT_SECRET="secret",
        GOOGLE_REDIRECT_URI="http://localhost:8011/api/v1/email/callback",
        MICROSOFT_CLIENT_ID="ms-id", MICROSOFT_CLIENT_SECRET="ms-secret",
        MICROSOFT_REDIRECT_URI="http://localhost:8011/api/v1/email/callback",
        TOKEN_ENCRYPTION_KEY=ENCRYPTION_KEY,
        DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost/db",
        REDIS_URL="redis://localhost:6379/8", S3_ENDPOINT_URL="http://localhost:9000",
        S3_ACCESS_KEY_ID="key", S3_SECRET_ACCESS_KEY="secret", S3_BUCKET_NAME="bucket",
        FRONTEND_BASE_URL="http://localhost:8080",
        DEFAULT_COMPANY_ID="00000000-0000-0000-0000-000000000001",
        INVOICE_SERVICE_URL="http://localhost:8002",
    )


class TestTokenStillFresh:
    @pytest.mark.asyncio
    async def test_a_token_with_plenty_of_time_left_is_not_refreshed(self, monkeypatch) -> None:
        refresh_mock = AsyncMock()
        monkeypatch.setattr(gmail_oauth, "refresh_access_token", refresh_mock)
        account = _account(expires_in=timedelta(minutes=30))

        token = await token_manager.get_valid_access_token(account, _settings())

        assert token == "old-access"
        refresh_mock.assert_not_called()


class TestTokenNeedsRefresh:
    @pytest.mark.asyncio
    async def test_a_token_expiring_within_the_margin_is_refreshed(self, monkeypatch) -> None:
        monkeypatch.setattr(
            gmail_oauth, "refresh_access_token",
            AsyncMock(return_value={"access_token": "new-access", "expires_in": 3600}),
        )
        account = _account(expires_in=timedelta(minutes=2))

        token = await token_manager.get_valid_access_token(account, _settings())

        assert token == "new-access"

    @pytest.mark.asyncio
    async def test_an_already_expired_token_is_refreshed(self, monkeypatch) -> None:
        monkeypatch.setattr(
            gmail_oauth, "refresh_access_token",
            AsyncMock(return_value={"access_token": "new-access", "expires_in": 3600}),
        )
        account = _account(expires_in=timedelta(minutes=-10))

        token = await token_manager.get_valid_access_token(account, _settings())

        assert token == "new-access"

    @pytest.mark.asyncio
    async def test_the_new_token_is_persisted_encrypted_on_the_account(self, monkeypatch) -> None:
        monkeypatch.setattr(
            gmail_oauth, "refresh_access_token",
            AsyncMock(return_value={"access_token": "new-access", "expires_in": 3600}),
        )
        account = _account(expires_in=timedelta(minutes=1))

        await token_manager.get_valid_access_token(account, _settings())

        cipher = TokenCipher(ENCRYPTION_KEY)
        assert cipher.decrypt(account.access_token_encrypted) == "new-access"
        assert account.token_expires_at > datetime.now(timezone.utc) + timedelta(minutes=59)

    @pytest.mark.asyncio
    async def test_the_refresh_token_itself_is_never_overwritten_if_the_provider_omits_one(self, monkeypatch) -> None:
        """Google's refresh response does not include a new refresh_token —
        the original keeps working. Nothing here should touch
        refresh_token_encrypted."""
        monkeypatch.setattr(
            gmail_oauth, "refresh_access_token",
            AsyncMock(return_value={"access_token": "new-access", "expires_in": 3600}),
        )
        account = _account(expires_in=timedelta(minutes=1), refresh="original-refresh-token")

        await token_manager.get_valid_access_token(account, _settings())

        cipher = TokenCipher(ENCRYPTION_KEY)
        assert cipher.decrypt(account.refresh_token_encrypted) == "original-refresh-token"

    @pytest.mark.asyncio
    async def test_a_rotated_refresh_token_is_persisted_when_the_provider_sends_one(self, monkeypatch) -> None:
        """Unlike Google, Microsoft (almost) always rotates the
        refresh_token on every use (plan §11a) — the old one stops working,
        so failing to persist the new one would break the account on its
        *next* refresh, not this one."""
        monkeypatch.setattr(
            outlook_oauth, "refresh_access_token",
            AsyncMock(return_value={"access_token": "new-access", "refresh_token": "rotated-refresh", "expires_in": 3600}),
        )
        account = _account(expires_in=timedelta(minutes=1), refresh="original-refresh-token", provider=EmailProviderName.outlook)

        await token_manager.get_valid_access_token(account, _settings())

        cipher = TokenCipher(ENCRYPTION_KEY)
        assert cipher.decrypt(account.refresh_token_encrypted) == "rotated-refresh"


class TestRefreshFailure:
    @pytest.mark.asyncio
    async def test_a_failed_refresh_marks_the_account_needs_reauth(self, monkeypatch) -> None:
        """The account was revoked from the user's Google account, or the
        refresh token itself expired — retrying will not help, so the fix
        is surfaced as 'reconnect', not silently retried forever."""
        monkeypatch.setattr(
            gmail_oauth, "refresh_access_token", AsyncMock(side_effect=RuntimeError("invalid_grant")),
        )
        account = _account(expires_in=timedelta(minutes=1))

        with pytest.raises(token_manager.TokenRefreshError):
            await token_manager.get_valid_access_token(account, _settings())

        assert account.status == EmailAccountStatus.needs_reauth

    @pytest.mark.asyncio
    async def test_an_incomplete_refresh_response_also_marks_needs_reauth(self, monkeypatch) -> None:
        monkeypatch.setattr(
            gmail_oauth, "refresh_access_token", AsyncMock(return_value={"expires_in": 3600}),  # no access_token
        )
        account = _account(expires_in=timedelta(minutes=1))

        with pytest.raises(token_manager.TokenRefreshError):
            await token_manager.get_valid_access_token(account, _settings())

        assert account.status == EmailAccountStatus.needs_reauth

    @pytest.mark.asyncio
    async def test_an_outlook_refresh_failure_is_also_recovered_from(self, monkeypatch) -> None:
        """Same recovery path, exercised against the other provider's oauth
        module — guards against the provider dispatch silently always
        picking Gmail's module regardless of account.provider."""
        monkeypatch.setattr(
            outlook_oauth, "refresh_access_token", AsyncMock(side_effect=RuntimeError("invalid_grant")),
        )
        account = _account(expires_in=timedelta(minutes=1), provider=EmailProviderName.outlook)

        with pytest.raises(token_manager.TokenRefreshError):
            await token_manager.get_valid_access_token(account, _settings())

        assert account.status == EmailAccountStatus.needs_reauth
