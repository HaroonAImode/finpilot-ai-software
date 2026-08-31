# tests/unit/test_config.py
import pytest

from app.core.config import ConfigurationError, Settings, assert_usable_encryption_key

VALID_SETTINGS = dict(
    SLACK_CLIENT_ID="id", SLACK_CLIENT_SECRET="secret", SLACK_SIGNING_SECRET="signing",
    SLACK_REDIRECT_URI="http://localhost:8010/api/v1/slack/callback",
    TOKEN_ENCRYPTION_KEY="dGVzdC1mZXJuZXQta2V5LW11c3QtYmUtMzItYnl0ZXM=",
    DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost/db",
    REDIS_URL="redis://localhost:6379/3",
    S3_ENDPOINT_URL="http://localhost:9000", S3_ACCESS_KEY_ID="key", S3_SECRET_ACCESS_KEY="secret",
    S3_BUCKET_NAME="bucket", FRONTEND_BASE_URL="http://localhost:3000",
    DEFAULT_COMPANY_ID="00000000-0000-0000-0000-000000000001",
    INVOICE_SERVICE_URL="http://localhost:8002",
)


def test_settings_reads_default_company_id_and_invoice_service_url() -> None:
    settings = Settings(
        SLACK_CLIENT_ID="id", SLACK_CLIENT_SECRET="secret", SLACK_SIGNING_SECRET="signing",
        SLACK_REDIRECT_URI="http://localhost:8010/api/v1/slack/callback",
        TOKEN_ENCRYPTION_KEY="dGVzdC1mZXJuZXQta2V5LW11c3QtYmUtMzItYnl0ZXM=",
        DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost/db",
        REDIS_URL="redis://localhost:6379/3",
        S3_ENDPOINT_URL="http://localhost:9000", S3_ACCESS_KEY_ID="key", S3_SECRET_ACCESS_KEY="secret",
        S3_BUCKET_NAME="bucket", FRONTEND_BASE_URL="http://localhost:3000",
        DEFAULT_COMPANY_ID="00000000-0000-0000-0000-000000000001",
        INVOICE_SERVICE_URL="http://localhost:8002",
    )

    assert settings.default_company_id == "00000000-0000-0000-0000-000000000001"
    assert settings.invoice_service_url == "http://localhost:8002"
    assert settings.resolved_celery_broker_url == settings.redis_url


@pytest.mark.parametrize(
    "bad_key",
    [
        "dGVzdC1mZXJuZXQta2V5LW11c3QtYmUtMzItYnl0ZXM",  # 43 chars — missing '=' padding
        "not-a-fernet-key",
        "",
    ],
)
def test_invalid_encryption_key_is_rejected(bad_key: str) -> None:
    """A bad key must fail at startup, not as a 500 on the first OAuth request."""
    with pytest.raises(ConfigurationError, match="TOKEN_ENCRYPTION_KEY is not a valid Fernet key"):
        assert_usable_encryption_key(bad_key)


def test_encryption_key_error_never_echoes_the_key() -> None:
    """The message may report the length, never the key itself — it lands in logs."""
    secret = "GZ5Samny52HJp4Gfeout8d2JVvuW-j2_rzwPba6osDk"
    with pytest.raises(ConfigurationError) as exc:
        assert_usable_encryption_key(secret)
    assert secret not in str(exc.value)
    assert "43 characters" in str(exc.value)


def test_valid_encryption_key_passes() -> None:
    assert_usable_encryption_key(VALID_SETTINGS["TOKEN_ENCRYPTION_KEY"]) is None
