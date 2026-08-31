from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).parent.parent.parent / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    slack_client_id: str = Field(alias="SLACK_CLIENT_ID")
    slack_client_secret: str = Field(alias="SLACK_CLIENT_SECRET")
    slack_signing_secret: str = Field(alias="SLACK_SIGNING_SECRET")
    slack_redirect_uri: str = Field(alias="SLACK_REDIRECT_URI")
    slack_dev_bot_token: str | None = Field(default=None, alias="SLACK_DEV_BOT_TOKEN")
    token_encryption_key: str = Field(alias="TOKEN_ENCRYPTION_KEY")
    database_url: str = Field(alias="DATABASE_URL")
    redis_url: str = Field(alias="REDIS_URL")
    celery_broker_url: str | None = Field(default=None, alias="CELERY_BROKER_URL")
    celery_result_backend: str | None = Field(default=None, alias="CELERY_RESULT_BACKEND")
    s3_endpoint_url: str = Field(alias="S3_ENDPOINT_URL")
    s3_public_endpoint_url: str | None = Field(default=None, alias="S3_PUBLIC_ENDPOINT_URL")
    s3_access_key_id: str = Field(alias="S3_ACCESS_KEY_ID")
    s3_secret_access_key: str = Field(alias="S3_SECRET_ACCESS_KEY")
    s3_bucket_name: str = Field(alias="S3_BUCKET_NAME")
    app_env: str = Field(default="development", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    frontend_base_url: str = Field(alias="FRONTEND_BASE_URL")

    # Must match the Auth Service's JWT_SECRET_KEY, or tokens it mints are
    # rejected here. Optional so the service still runs in header-trust dev mode.
    jwt_secret_key: str = Field(default="", alias="JWT_SECRET_KEY")

    # Development fallback: accept X-Company-ID / DEFAULT_COMPANY_ID when no
    # bearer token is present. Nothing verifies that header until the Gateway
    # exists, so anyone could set it and read another company's documents — hence
    # opt-in, and forced off in production by model_post_init below.
    trust_company_header: bool = Field(default=True, alias="TRUST_COMPANY_HEADER")
    default_company_id: str = Field(alias="DEFAULT_COMPANY_ID")

    # Used by scanner_bridge.py (Task 12) to call Invoice Service's scan endpoint.
    invoice_service_url: str = Field(alias="INVOICE_SERVICE_URL")
    #: Generous by default: the scan this triggers runs OCR, which on a
    #: CPU-constrained host genuinely takes minutes (measured — see
    #: docs/invoice-ocr-plan.md's PaddleOCR latency notes). The previous
    #: hardcoded 30s in scanner_bridge.py cut off scans that would otherwise
    #: have succeeded.
    invoice_service_timeout_seconds: float = Field(
        default=240.0, alias="INVOICE_SERVICE_TIMEOUT_SECONDS"
    )

    def model_post_init(self, _context) -> None:
        # An unverified, caller-supplied company header is a cross-tenant read of
        # someone else's financial documents. Acceptable on a laptop, never in
        # production — so refuse to start rather than rely on remembering to set
        # the flag.
        if self.is_production:
            if self.trust_company_header:
                raise ValueError(
                    "TRUST_COMPANY_HEADER must be false when APP_ENV is production: the "
                    "X-Company-ID header is not verified, so trusting it would let any "
                    "caller read another company's documents."
                )
            if not self.jwt_secret_key:
                raise ValueError(
                    "JWT_SECRET_KEY is required in production — it is the only way to "
                    "verify which company a request belongs to."
                )

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in {"production", "prod"}

    @property
    def resolved_celery_broker_url(self) -> str:
        return self.celery_broker_url or self.redis_url

    @property
    def resolved_celery_result_backend(self) -> str:
        return self.celery_result_backend or self.redis_url


class ConfigurationError(RuntimeError):
    """Raised when the process is configured in a way that cannot work."""


def assert_usable_encryption_key(key: str) -> None:
    """Fail fast on an unusable TOKEN_ENCRYPTION_KEY.

    Called from the process entry points (app.main, app.worker) rather than from
    get_settings(): without it the service boots fine and only dies when someone
    starts an OAuth flow — as a 500 with a raw traceback, far from the real cause.
    Putting it in get_settings() instead would make merely importing any module
    that touches settings (including the test suite's conftest) require a valid
    key, which is not the same thing as the service being able to run.

    Deliberately not a pydantic field validator either: pydantic embeds the
    offending input in its ValidationError, which would print the encryption key
    itself into logs and stack traces. This raises with the key length only.
    """
    try:
        Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise ConfigurationError(
            f"TOKEN_ENCRYPTION_KEY is not a valid Fernet key (got {len(key)} characters; "
            "it must be 32 url-safe base64-encoded bytes, i.e. 44 characters ending in '='). "
            'Generate one with: python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"'
        ) from exc


@lru_cache
def get_settings() -> Settings:
    return Settings()
