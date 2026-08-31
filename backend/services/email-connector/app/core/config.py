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

    google_client_id: str = Field(alias="GOOGLE_CLIENT_ID")
    google_client_secret: str = Field(alias="GOOGLE_CLIENT_SECRET")
    google_redirect_uri: str = Field(alias="GOOGLE_REDIRECT_URI")

    # Outlook/Microsoft 365 (Phase 5, plan §11a). Optional and unset by
    # default — Gmail-only deployments should not have to provision an Azure
    # AD App Registration they'll never use. outlook/oauth.py's
    # _require_configured raises a clear ConfigurationError (not a confusing
    # 401/404 from Microsoft) if a caller reaches it with these unset.
    microsoft_client_id: str = Field(default="", alias="MICROSOFT_CLIENT_ID")
    microsoft_client_secret: str = Field(default="", alias="MICROSOFT_CLIENT_SECRET")
    microsoft_redirect_uri: str = Field(default="", alias="MICROSOFT_REDIRECT_URI")
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
    # bearer token is present. Same convention as slack-connector, but a
    # mailbox is more sensitive than a Slack channel, so this is even less
    # acceptable to leave on by accident — hence still opt-in, still forced
    # off in production by model_post_init below.
    trust_company_header: bool = Field(default=True, alias="TRUST_COMPANY_HEADER")
    default_company_id: str = Field(alias="DEFAULT_COMPANY_ID")

    # Used by scanner_bridge.py to call Invoice Service's scan endpoint —
    # copies slack-connector's bridge pattern verbatim (see
    # docs/email-connector-plan.md §11b for why this is HTTP, not RabbitMQ).
    invoice_service_url: str = Field(alias="INVOICE_SERVICE_URL")
    #: Generous by default: the scan this triggers runs OCR, which on a
    #: CPU-constrained host genuinely takes minutes (measured — see
    #: docs/invoice-ocr-plan.md's PaddleOCR latency notes). The previous
    #: hardcoded 30s in scanner_bridge.py cut off scans that would otherwise
    #: have succeeded.
    invoice_service_timeout_seconds: float = Field(
        default=240.0, alias="INVOICE_SERVICE_TIMEOUT_SECONDS"
    )

    # How often Celery Beat queues an incremental sync for every connected
    # account. Cheap by design: an incremental run costs one history.list call
    # when nothing has changed. 0 disables scheduling entirely, which is what
    # a developer running the worker ad-hoc usually wants.
    scheduled_sync_minutes: int = Field(default=30, ge=0, alias="SCHEDULED_SYNC_MINUTES")

    def model_post_init(self, _context) -> None:
        # An unverified, caller-supplied company header is a cross-tenant read of
        # someone else's mailbox — worse than the equivalent Slack case, since a
        # mailbox holds personal, financial and legal correspondence in one
        # place. Acceptable on a laptop, never in production.
        if self.is_production:
            if self.trust_company_header:
                raise ValueError(
                    "TRUST_COMPANY_HEADER must be false when APP_ENV is production: the "
                    "X-Company-ID header is not verified, so trusting it would let any "
                    "caller read another company's mailbox."
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

    Same rationale as slack-connector: called from process entry points rather
    than get_settings(), so importing settings (e.g. in tests) never requires a
    valid key, only actually running the service does. Deliberately not a
    pydantic field validator either, since that would embed the key itself in
    the ValidationError.
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
