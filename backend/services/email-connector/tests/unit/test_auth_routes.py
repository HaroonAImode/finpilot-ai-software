"""OAuth connect/callback/status/disconnect routes.

Beyond the state round-trip (mirrors slack-connector's test_auth_routes.py),
this covers behaviour with no Slack equivalent: the missing-refresh-token
guard, upsert-by-company on reconnect, the cross-company Google-account
conflict, and disconnect actually deleting the row rather than flipping a
status flag (see api/routes/auth.py's disconnect docstring for why).
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.security import TokenCipher
from app.core.tenancy import get_company_id
from app.db.session import get_db
from app.main import app
from app.models import EmailAccount, EmailProviderName

# The pure state round-trip tests below exercise _build_state/_parse_state in
# isolation and may use any valid Fernet key. The route-level tests further
# down go through the actual running app, so they must build state with
# whatever key that app instance loaded from its real .env — a mismatched
# key here isn't a bug in the route, it just means the test forged a state
# the app could never have issued itself.
ENCRYPTION_KEY = "dGVzdC1mZXJuZXQta2V5LW11c3QtYmUtMzItYnl0ZXM="


def test_oauth_state_round_trips_company_id_and_nonce() -> None:
    from app.api.routes.auth import _build_state, _parse_state

    cipher = TokenCipher(ENCRYPTION_KEY)
    company_id = uuid.uuid4()

    encrypted_state, nonce = _build_state(cipher, company_id)
    parsed_company_id, parsed_nonce, parsed_provider = _parse_state(cipher, encrypted_state)

    assert parsed_company_id == company_id
    assert parsed_nonce == nonce
    assert parsed_provider == EmailProviderName.gmail


def test_parse_state_rejects_tampered_payload() -> None:
    from app.api.routes.auth import _parse_state

    cipher = TokenCipher(ENCRYPTION_KEY)
    with pytest.raises(ValueError):
        _parse_state(cipher, "not-a-real-encrypted-state")


@pytest.fixture
def db_session_factory():
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models.base import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.get_event_loop().run_until_complete(_setup())
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def client(db_session_factory):
    async def _db():
        async with db_session_factory() as session:
            yield session

    company_id = uuid.uuid4()
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_company_id] = lambda: company_id
    yield TestClient(app, follow_redirects=False), company_id
    app.dependency_overrides.clear()


def _state_and_cookie(company_id: uuid.UUID) -> tuple[str, str]:
    from app.api.routes.auth import _build_state

    cipher = TokenCipher(get_settings().token_encryption_key)
    return _build_state(cipher, company_id)


def _patch_gmail(monkeypatch, *, exchange_result=None, exchange_error=None, email="vendor@company.com"):
    import app.api.routes.auth as auth_routes

    if exchange_error is not None:
        monkeypatch.setattr(auth_routes, "exchange_code", AsyncMock(side_effect=exchange_error))
    else:
        monkeypatch.setattr(auth_routes, "exchange_code", AsyncMock(return_value=exchange_result))
    monkeypatch.setattr(auth_routes, "fetch_email_address", AsyncMock(return_value=email))


class TestConnect:
    def test_returns_an_authorize_url_and_sets_the_nonce_cookie(self, client) -> None:
        test_client, _ = client
        response = test_client.get("/api/v1/email/connect")

        assert response.status_code == 200
        assert response.json()["authorize_url"].startswith("https://accounts.google.com/")
        assert "email_oauth_nonce" in response.cookies

    def test_provider_outlook_returns_a_microsoft_authorize_url(self, client) -> None:
        """Phase 5 (plan §11a): the same /connect route serves both
        providers via ?provider=, rather than a second route per provider."""
        from app.core.config import Settings
        from app.main import app as fastapi_app

        outlook_settings = Settings(
            GOOGLE_CLIENT_ID="id", GOOGLE_CLIENT_SECRET="secret",
            GOOGLE_REDIRECT_URI="http://localhost:8011/api/v1/email/callback",
            MICROSOFT_CLIENT_ID="test-ms-client-id", MICROSOFT_CLIENT_SECRET="test-ms-secret",
            MICROSOFT_REDIRECT_URI="http://localhost:8011/api/v1/email/callback",
            TOKEN_ENCRYPTION_KEY=ENCRYPTION_KEY,
            DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost/db",
            REDIS_URL="redis://localhost:6379/8", S3_ENDPOINT_URL="http://localhost:9000",
            S3_ACCESS_KEY_ID="key", S3_SECRET_ACCESS_KEY="secret", S3_BUCKET_NAME="bucket",
            FRONTEND_BASE_URL="http://localhost:8080",
            DEFAULT_COMPANY_ID="00000000-0000-0000-0000-000000000001",
            INVOICE_SERVICE_URL="http://localhost:8002",
        )
        test_client, _ = client
        fastapi_app.dependency_overrides[get_settings] = lambda: outlook_settings
        try:
            response = test_client.get("/api/v1/email/connect", params={"provider": "outlook"})
        finally:
            fastapi_app.dependency_overrides.pop(get_settings, None)

        assert response.status_code == 200
        assert response.json()["authorize_url"].startswith("https://login.microsoftonline.com/")
        assert "email_oauth_nonce" in response.cookies

    def test_provider_outlook_without_azure_credentials_is_a_clean_503(self, client) -> None:
        """The honest current state of this deployment: no Azure AD App
        Registration exists yet (plan §11a — that's a paperwork task only
        the account owner can do), so this must surface as a clear,
        actionable error rather than an unhandled 500."""
        test_client, _ = client

        response = test_client.get("/api/v1/email/connect", params={"provider": "outlook"})

        assert response.status_code == 503
        assert "not configured" in response.json()["detail"].lower()

    def test_an_unknown_provider_is_rejected(self, client) -> None:
        test_client, _ = client
        response = test_client.get("/api/v1/email/connect", params={"provider": "yahoo"})
        assert response.status_code == 400


class TestCallback:
    def test_mismatched_nonce_cookie_is_rejected(self, client, monkeypatch) -> None:
        test_client, company_id = client
        _patch_gmail(monkeypatch, exchange_result={"access_token": "at", "refresh_token": "rt", "expires_in": 3600})
        state, _real_nonce = _state_and_cookie(company_id)
        test_client.cookies.set("email_oauth_nonce", "a-different-nonce")

        response = test_client.get("/api/v1/email/callback", params={"code": "c", "state": state})

        assert response.status_code == 400

    def test_missing_refresh_token_is_a_clear_502_not_a_silent_broken_account(self, client, monkeypatch) -> None:
        """Guards the exact failure mode docs/email-connector-plan.md §8 warns
        about: an account with no refresh token works for one hour then dies
        with no way to recover."""
        test_client, company_id = client
        state, nonce = _state_and_cookie(company_id)
        test_client.cookies.set("email_oauth_nonce", nonce)
        _patch_gmail(monkeypatch, exchange_result={"access_token": "at", "expires_in": 3600})  # no refresh_token

        response = test_client.get("/api/v1/email/callback", params={"code": "c", "state": state})

        assert response.status_code == 502
        assert "refresh token" in response.json()["detail"].lower()

    def test_successful_connection_creates_the_account_and_redirects(self, client, monkeypatch) -> None:
        test_client, company_id = client
        state, nonce = _state_and_cookie(company_id)
        test_client.cookies.set("email_oauth_nonce", nonce)
        _patch_gmail(
            monkeypatch,
            exchange_result={"access_token": "at", "refresh_token": "rt", "expires_in": 3600, "scope": "gmail.readonly openid email"},
            email="vendor@company.com",
        )

        response = test_client.get("/api/v1/email/callback", params={"code": "c", "state": state})

        assert response.status_code == 302
        assert "vendor%40company.com" in response.headers["location"] or "vendor@company.com" in response.headers["location"]

        status = test_client.get("/api/v1/email/status")
        body = status.json()
        assert body["connected"] is True
        assert body["email_address"] == "vendor@company.com"
        assert body["provider"] == "gmail"

    def test_reconnecting_updates_the_existing_row_not_a_second_one(self, client, monkeypatch, db_session_factory) -> None:
        test_client, company_id = client
        _patch_gmail(
            monkeypatch,
            exchange_result={"access_token": "at1", "refresh_token": "rt1", "expires_in": 3600},
            email="first@company.com",
        )
        state, nonce = _state_and_cookie(company_id)
        test_client.cookies.set("email_oauth_nonce", nonce)
        test_client.get("/api/v1/email/callback", params={"code": "c", "state": state})

        _patch_gmail(
            monkeypatch,
            exchange_result={"access_token": "at2", "refresh_token": "rt2", "expires_in": 3600},
            email="second@company.com",
        )
        state2, nonce2 = _state_and_cookie(company_id)
        test_client.cookies.set("email_oauth_nonce", nonce2)
        test_client.get("/api/v1/email/callback", params={"code": "c2", "state": state2})

        import asyncio
        from sqlalchemy import select

        async def _count():
            async with db_session_factory() as db:
                rows = (await db.execute(select(EmailAccount).where(EmailAccount.company_id == company_id))).scalars().all()
                return rows

        rows = asyncio.get_event_loop().run_until_complete(_count())
        assert len(rows) == 1
        assert rows[0].email_address == "second@company.com"

    def test_a_google_account_already_owned_by_another_company_is_refused(self, client, monkeypatch, db_session_factory) -> None:
        other_company = uuid.uuid4()

        async def _seed():
            async with db_session_factory() as db:
                db.add(EmailAccount(
                    company_id=other_company, provider=EmailProviderName.gmail,
                    email_address="taken@company.com", access_token_encrypted="x",
                    refresh_token_encrypted="y", token_expires_at=datetime.now(timezone.utc), scopes=[],
                ))
                await db.commit()

        import asyncio
        asyncio.get_event_loop().run_until_complete(_seed())

        test_client, company_id = client
        _patch_gmail(
            monkeypatch,
            exchange_result={"access_token": "at", "refresh_token": "rt", "expires_in": 3600},
            email="taken@company.com",
        )
        state, nonce = _state_and_cookie(company_id)
        test_client.cookies.set("email_oauth_nonce", nonce)

        response = test_client.get("/api/v1/email/callback", params={"code": "c", "state": state})

        assert response.status_code == 409


class TestStatusAndDisconnect:
    def test_status_when_nothing_connected(self, client) -> None:
        test_client, _ = client
        response = test_client.get("/api/v1/email/status")
        assert response.json() == {"connected": False}

    def test_disconnect_actually_deletes_the_row(self, client, monkeypatch, db_session_factory) -> None:
        test_client, company_id = client
        _patch_gmail(
            monkeypatch,
            exchange_result={"access_token": "at", "refresh_token": "rt", "expires_in": 3600},
            email="vendor@company.com",
        )
        state, nonce = _state_and_cookie(company_id)
        test_client.cookies.set("email_oauth_nonce", nonce)
        test_client.get("/api/v1/email/callback", params={"code": "c", "state": state})

        response = test_client.delete("/api/v1/email/account")
        assert response.status_code == 200
        assert response.json() == {"connected": False}

        import asyncio
        from sqlalchemy import select

        async def _count():
            async with db_session_factory() as db:
                rows = (await db.execute(select(EmailAccount).where(EmailAccount.company_id == company_id))).scalars().all()
                return rows

        assert asyncio.get_event_loop().run_until_complete(_count()) == []

    def test_disconnecting_twice_404s_the_second_time(self, client) -> None:
        test_client, _ = client
        response = test_client.delete("/api/v1/email/account")
        assert response.status_code == 404
