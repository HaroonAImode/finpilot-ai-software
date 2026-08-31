"""Gmail OAuth 2.0 — authorization URL shape, token exchange, refresh, and
the "which mailbox did we connect" resolution.

The one thing Slack's OAuth never had to handle: access tokens expire, so
refresh_access_token exists purely because of that — this is new logic with
no Slack equivalent, and getting it wrong means the connector works for an
hour then silently stops (docs/email-connector-plan.md §8).
"""
import pytest

from app.core.config import Settings
from app.services.gmail import oauth

VALID_SETTINGS = dict(
    GOOGLE_CLIENT_ID="client-id", GOOGLE_CLIENT_SECRET="client-secret",
    GOOGLE_REDIRECT_URI="http://localhost:8011/api/v1/email/callback",
    TOKEN_ENCRYPTION_KEY="dGVzdC1mZXJuZXQta2V5LW11c3QtYmUtMzItYnl0ZXM=",
    DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost/db",
    REDIS_URL="redis://localhost:6379/8", S3_ENDPOINT_URL="http://localhost:9000",
    S3_ACCESS_KEY_ID="key", S3_SECRET_ACCESS_KEY="secret", S3_BUCKET_NAME="bucket",
    FRONTEND_BASE_URL="http://localhost:8080",
    DEFAULT_COMPANY_ID="00000000-0000-0000-0000-000000000001",
    INVOICE_SERVICE_URL="http://localhost:8002",
)


def _settings() -> Settings:
    return Settings(**VALID_SETTINGS)


class TestAuthorizationUrl:
    def test_requests_the_readonly_gmail_scope(self) -> None:
        url = oauth.authorization_url(_settings(), "state")

        assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
        assert "gmail.readonly" in url

    def test_forces_offline_access_and_consent(self) -> None:
        """Without both of these, Google only returns a refresh_token on a
        user's very first-ever authorization — a reconnect after revoking
        access would silently get none, and the account would die in an hour
        with no way to recover it. See exchange_code's missing-refresh-token
        guard in api/routes/auth.py for the other half of this protection."""
        url = oauth.authorization_url(_settings(), "state")

        assert "access_type=offline" in url
        assert "prompt=consent" in url

    def test_carries_the_opaque_state_through_unchanged(self) -> None:
        url = oauth.authorization_url(_settings(), "opaque-state-value")
        assert "state=opaque-state-value" in url


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
    """Stands in for httpx.AsyncClient's async-context-manager shape so tests
    never make a real network call to Google."""

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
        "app.services.gmail.oauth.httpx.AsyncClient", lambda **k: _FakeAsyncClient(response, capture)
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


class TestRefreshAccessToken:
    @pytest.mark.asyncio
    async def test_posts_the_refresh_token_grant(self, monkeypatch) -> None:
        capture = _patch_client(
            monkeypatch, _FakeResponse({"access_token": "new-at", "expires_in": 3600})
        )

        result = await oauth.refresh_access_token(_settings(), "stored-refresh-token")

        assert result["access_token"] == "new-at"
        assert capture["kwargs"]["data"]["grant_type"] == "refresh_token"
        assert capture["kwargs"]["data"]["refresh_token"] == "stored-refresh-token"

    @pytest.mark.asyncio
    async def test_response_has_no_refresh_token_and_that_is_expected(self, monkeypatch) -> None:
        """Google does not re-issue a refresh_token on a refresh call — the
        original one keeps working. A caller that overwrote the stored
        refresh_token_encrypted with this response's (missing) value would
        destroy the account's only way to get future access tokens."""
        _patch_client(monkeypatch, _FakeResponse({"access_token": "new-at", "expires_in": 3600}))

        result = await oauth.refresh_access_token(_settings(), "stored-refresh-token")

        assert "refresh_token" not in result


class TestFetchEmailAddress:
    @pytest.mark.asyncio
    async def test_returns_the_connected_address(self, monkeypatch) -> None:
        _patch_client(monkeypatch, _FakeResponse({"email": "vendor@company.com", "verified_email": True}))

        email = await oauth.fetch_email_address("some-access-token")

        assert email == "vendor@company.com"

    @pytest.mark.asyncio
    async def test_raises_when_the_response_has_no_email(self, monkeypatch) -> None:
        _patch_client(monkeypatch, _FakeResponse({"verified_email": True}))

        with pytest.raises(ValueError):
            await oauth.fetch_email_address("some-access-token")
