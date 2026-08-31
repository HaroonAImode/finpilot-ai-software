"""Microsoft identity platform OAuth 2.0 — Phase 5 (plan §11a), Outlook's
counterpart to test_gmail_oauth.py. The one thing worth calling out that has
no Gmail equivalent: Microsoft (almost) always rotates the refresh_token on
every refresh call — the opposite of Google's behaviour, which is why
providers/token_manager.py (not this module) is what actually decides
whether to persist a new one.
"""
import pytest

from app.core.config import ConfigurationError, Settings
from app.services.outlook import oauth

VALID_SETTINGS = dict(
    GOOGLE_CLIENT_ID="g-id", GOOGLE_CLIENT_SECRET="g-secret",
    GOOGLE_REDIRECT_URI="http://localhost:8011/api/v1/email/callback",
    MICROSOFT_CLIENT_ID="ms-client-id", MICROSOFT_CLIENT_SECRET="ms-client-secret",
    MICROSOFT_REDIRECT_URI="http://localhost:8011/api/v1/email/callback",
    TOKEN_ENCRYPTION_KEY="dGVzdC1mZXJuZXQta2V5LW11c3QtYmUtMzItYnl0ZXM=",
    DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost/db",
    REDIS_URL="redis://localhost:6379/8", S3_ENDPOINT_URL="http://localhost:9000",
    S3_ACCESS_KEY_ID="key", S3_SECRET_ACCESS_KEY="secret", S3_BUCKET_NAME="bucket",
    FRONTEND_BASE_URL="http://localhost:8080",
    DEFAULT_COMPANY_ID="00000000-0000-0000-0000-000000000001",
    INVOICE_SERVICE_URL="http://localhost:8002",
)

UNCONFIGURED_SETTINGS = {**VALID_SETTINGS, "MICROSOFT_CLIENT_ID": "", "MICROSOFT_CLIENT_SECRET": "", "MICROSOFT_REDIRECT_URI": ""}


def _settings() -> Settings:
    return Settings(**VALID_SETTINGS)


def _unconfigured_settings() -> Settings:
    return Settings(**UNCONFIGURED_SETTINGS)


class TestAuthorizationUrl:
    def test_requests_the_readonly_mail_scope_against_the_common_tenant(self) -> None:
        url = oauth.authorization_url(_settings(), "state")

        assert url.startswith("https://login.microsoftonline.com/common/oauth2/v2.0/authorize?")
        assert "Mail.Read" in url

    def test_forces_offline_access(self) -> None:
        """Without this, Microsoft only returns an access token — no
        refresh_token, so the account would work for roughly an hour then
        die with no way to recover it. Same reasoning as Gmail's
        access_type=offline."""
        url = oauth.authorization_url(_settings(), "state")
        assert "offline_access" in url

    def test_carries_the_opaque_state_through_unchanged(self) -> None:
        url = oauth.authorization_url(_settings(), "opaque-state-value")
        assert "state=opaque-state-value" in url

    def test_raises_a_clear_error_when_unconfigured(self) -> None:
        """A Gmail-only deployment never sets MICROSOFT_CLIENT_ID — this
        must fail with an actionable message, not a confusing 401 once it
        reaches Microsoft with an empty client_id."""
        with pytest.raises(ConfigurationError, match="not configured"):
            oauth.authorization_url(_unconfigured_settings(), "state")


class _FakeResponse:
    def __init__(self, json_body: dict, status_code: int = 200):
        self._json_body = json_body
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError("error", request=None, response=self)

    def json(self) -> dict:
        return self._json_body


class _FakeAsyncClient:
    def __init__(self, response: _FakeResponse, capture: dict):
        self._response = response
        self._capture = capture

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, **kwargs):
        self._capture["url"] = url
        self._capture["kwargs"] = kwargs
        return self._response

    async def get(self, url, **kwargs):
        self._capture["url"] = url
        self._capture["kwargs"] = kwargs
        return self._response


def _patch_client(monkeypatch, response: _FakeResponse) -> dict:
    capture: dict = {}
    monkeypatch.setattr(
        "app.services.outlook.oauth.httpx.AsyncClient", lambda **k: _FakeAsyncClient(response, capture)
    )
    return capture


class TestExchangeCode:
    @pytest.mark.asyncio
    async def test_posts_the_authorization_code_grant(self, monkeypatch) -> None:
        capture = _patch_client(
            monkeypatch,
            _FakeResponse({"access_token": "at", "refresh_token": "rt", "expires_in": 3600}),
        )

        result = await oauth.exchange_code(_settings(), "auth-code")

        assert result == {"access_token": "at", "refresh_token": "rt", "expires_in": 3600}
        assert capture["kwargs"]["data"]["grant_type"] == "authorization_code"
        assert capture["kwargs"]["data"]["code"] == "auth-code"

    @pytest.mark.asyncio
    async def test_raises_on_an_error_response(self, monkeypatch) -> None:
        import httpx

        _patch_client(monkeypatch, _FakeResponse({"error": "invalid_grant"}, status_code=400))

        with pytest.raises(httpx.HTTPStatusError):
            await oauth.exchange_code(_settings(), "bad-code")

    @pytest.mark.asyncio
    async def test_raises_a_clear_error_when_unconfigured(self) -> None:
        with pytest.raises(ConfigurationError, match="not configured"):
            await oauth.exchange_code(_unconfigured_settings(), "auth-code")


class TestRefreshAccessToken:
    @pytest.mark.asyncio
    async def test_posts_the_refresh_token_grant(self, monkeypatch) -> None:
        capture = _patch_client(
            monkeypatch, _FakeResponse({"access_token": "new-at", "refresh_token": "new-rt", "expires_in": 3600})
        )

        result = await oauth.refresh_access_token(_settings(), "stored-refresh-token")

        assert result["access_token"] == "new-at"
        assert capture["kwargs"]["data"]["grant_type"] == "refresh_token"
        assert capture["kwargs"]["data"]["refresh_token"] == "stored-refresh-token"

    @pytest.mark.asyncio
    async def test_a_rotated_refresh_token_is_present_in_the_response(self, monkeypatch) -> None:
        """Unlike Gmail's oauth module, Microsoft's refresh response
        normally DOES include a new refresh_token — this module makes no
        decision about it either way (providers/token_manager.py does), but
        the response is passed through unmodified so that decision can be
        made."""
        _patch_client(
            monkeypatch, _FakeResponse({"access_token": "new-at", "refresh_token": "rotated-rt", "expires_in": 3600})
        )

        result = await oauth.refresh_access_token(_settings(), "stored-refresh-token")

        assert result["refresh_token"] == "rotated-rt"


class TestFetchEmailAddress:
    @pytest.mark.asyncio
    async def test_returns_the_mail_field_when_present(self, monkeypatch) -> None:
        _patch_client(monkeypatch, _FakeResponse({"mail": "vendor@company.com", "userPrincipalName": "vendor@company.onmicrosoft.com"}))

        email = await oauth.fetch_email_address("some-access-token")

        assert email == "vendor@company.com"

    @pytest.mark.asyncio
    async def test_falls_back_to_user_principal_name_when_mail_is_unset(self, monkeypatch) -> None:
        """Some personal/consumer Microsoft accounts have no `mail` field —
        userPrincipalName is the documented fallback."""
        _patch_client(monkeypatch, _FakeResponse({"mail": None, "userPrincipalName": "vendor@outlook.com"}))

        email = await oauth.fetch_email_address("some-access-token")

        assert email == "vendor@outlook.com"

    @pytest.mark.asyncio
    async def test_raises_when_the_response_has_neither_field(self, monkeypatch) -> None:
        _patch_client(monkeypatch, _FakeResponse({}))

        with pytest.raises(ValueError):
            await oauth.fetch_email_address("some-access-token")
