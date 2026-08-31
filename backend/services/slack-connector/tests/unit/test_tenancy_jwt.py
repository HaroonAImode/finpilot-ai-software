"""Company resolution from a verified JWT.

This is the tenancy boundary: whatever company_id comes out of here decides
which company's documents the request can read. The negative cases are the
point — a forged or mis-signed token must never resolve to a company.
"""
import uuid

import pytest
from fastapi import HTTPException
from shared.auth import create_access_token

from app.core.config import Settings
from app.core.tenancy import get_company_id

SECRET = "connector-test-secret-at-least-32-characters"
OTHER_SECRET = "a-totally-different-secret-key-for-testing!!"


def _settings(**overrides) -> Settings:
    values = dict(
        SLACK_CLIENT_ID="id", SLACK_CLIENT_SECRET="secret", SLACK_SIGNING_SECRET="signing",
        SLACK_REDIRECT_URI="http://localhost:8010/api/v1/slack/callback",
        TOKEN_ENCRYPTION_KEY="dGVzdC1mZXJuZXQta2V5LW11c3QtYmUtMzItYnl0ZXM=",
        DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost/db",
        REDIS_URL="redis://localhost:6379/3", S3_ENDPOINT_URL="http://localhost:9000",
        S3_ACCESS_KEY_ID="key", S3_SECRET_ACCESS_KEY="secret", S3_BUCKET_NAME="bucket",
        FRONTEND_BASE_URL="http://localhost:8080",
        DEFAULT_COMPANY_ID="00000000-0000-0000-0000-000000000001",
        INVOICE_SERVICE_URL="http://localhost:8002",
        JWT_SECRET_KEY=SECRET,
    )
    values.update(overrides)
    return Settings(**values)


def _token(company_id: uuid.UUID, *, secret: str = SECRET) -> str:
    token, _ = create_access_token(
        secret_key=secret, user_id=uuid.uuid4(), company_id=company_id,
        role="admin", email="ayesha@khan.pk",
    )
    return f"Bearer {token}"


class TestVerifiedToken:
    @pytest.mark.asyncio
    async def test_company_comes_from_the_token_not_the_header(self) -> None:
        """A caller must not be able to override their token by adding a header."""
        real = uuid.uuid4()
        someone_else = uuid.uuid4()

        resolved = await get_company_id(
            authorization=_token(real), x_company_id=str(someone_else), settings=_settings()
        )

        assert resolved == real

    @pytest.mark.asyncio
    async def test_token_signed_with_another_key_is_rejected(self) -> None:
        with pytest.raises(HTTPException) as excinfo:
            await get_company_id(
                authorization=_token(uuid.uuid4(), secret=OTHER_SECRET),
                x_company_id=None,
                settings=_settings(),
            )
        assert excinfo.value.status_code == 401

    @pytest.mark.asyncio
    async def test_malformed_token_is_rejected_not_ignored(self) -> None:
        """A broken token means the caller intended to authenticate — never fall
        back to the dev header, which would silently downgrade security."""
        for header in ["Bearer not-a-jwt", "Bearer ", "Basic abc123"]:
            with pytest.raises(HTTPException) as excinfo:
                await get_company_id(
                    authorization=header,
                    x_company_id=str(uuid.uuid4()),
                    settings=_settings(),
                )
            assert excinfo.value.status_code == 401


class TestDevelopmentHeaderFallback:
    @pytest.mark.asyncio
    async def test_header_is_used_when_trusted_and_no_token(self) -> None:
        company = uuid.uuid4()
        resolved = await get_company_id(
            authorization=None, x_company_id=str(company), settings=_settings()
        )
        assert resolved == company

    @pytest.mark.asyncio
    async def test_falls_back_to_default_company(self) -> None:
        resolved = await get_company_id(authorization=None, x_company_id=None, settings=_settings())
        assert str(resolved) == "00000000-0000-0000-0000-000000000001"

    @pytest.mark.asyncio
    async def test_no_token_is_rejected_when_header_trust_is_off(self) -> None:
        with pytest.raises(HTTPException) as excinfo:
            await get_company_id(
                authorization=None,
                x_company_id=str(uuid.uuid4()),
                settings=_settings(TRUST_COMPANY_HEADER=False),
            )
        assert excinfo.value.status_code == 401

    @pytest.mark.asyncio
    async def test_garbage_header_is_a_400_not_a_500(self) -> None:
        with pytest.raises(HTTPException) as excinfo:
            await get_company_id(
                authorization=None, x_company_id="not-a-uuid", settings=_settings()
            )
        assert excinfo.value.status_code == 400


class TestProductionRefusesUnsafeConfig:
    def test_production_rejects_trusting_the_header(self) -> None:
        """Trusting an unverified header in production is a cross-tenant leak."""
        with pytest.raises(ValueError, match="TRUST_COMPANY_HEADER"):
            _settings(APP_ENV="production", TRUST_COMPANY_HEADER=True)

    def test_production_requires_a_jwt_secret(self) -> None:
        with pytest.raises(ValueError, match="JWT_SECRET_KEY"):
            _settings(APP_ENV="production", TRUST_COMPANY_HEADER=False, JWT_SECRET_KEY="")

    def test_production_is_fine_when_configured_correctly(self) -> None:
        settings = _settings(APP_ENV="production", TRUST_COMPANY_HEADER=False)
        assert settings.is_production and not settings.trust_company_header
