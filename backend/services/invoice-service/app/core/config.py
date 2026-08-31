from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).parent.parent.parent / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(alias="DATABASE_URL")
    app_env: str = Field(default="development", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    frontend_base_url: str = Field(default="http://localhost:8080", alias="FRONTEND_BASE_URL")

    s3_endpoint_url: str = Field(alias="S3_ENDPOINT_URL")
    s3_public_endpoint_url: str | None = Field(default=None, alias="S3_PUBLIC_ENDPOINT_URL")
    s3_access_key_id: str = Field(alias="S3_ACCESS_KEY_ID")
    s3_secret_access_key: str = Field(alias="S3_SECRET_ACCESS_KEY")
    s3_bucket_name: str = Field(alias="S3_BUCKET_NAME")

    # Phase 2b's AI Engine — a plain internal HTTP call, not a queue. See
    # docs/invoice-ocr-plan.md §11b's reasoning (already established for
    # Email/Slack connectors' Invoice Service bridge): RabbitMQ doesn't
    # exist in this docker-compose yet, so every internal hand-off in this
    # codebase uses direct HTTP with graceful degradation instead of
    # inventing queue infrastructure ahead of an actual second consumer.
    ai_engine_url: str = Field(default="http://ai-engine:8007", alias="AI_ENGINE_URL")
    ai_engine_timeout_seconds: float = Field(default=60.0, alias="AI_ENGINE_TIMEOUT_SECONDS")

    # Transactions Service (architecture report §5.4) — this service's own
    # send-to-accounting docstring has said since it was written that this
    # becomes a real hand-off "once Transactions Service exists." It now
    # does: a purchase invoice being sent to accounting books a matching
    # Expense record there. Best-effort, same reasoning as AI Engine above —
    # no queue exists yet, so a plain HTTP call that degrades on failure is
    # this codebase's established pattern, not a shortcut invented here.
    transactions_service_url: str = Field(
        default="http://transactions-service:8003", alias="TRANSACTIONS_SERVICE_URL"
    )
    transactions_service_timeout_seconds: float = Field(
        default=10.0, alias="TRANSACTIONS_SERVICE_TIMEOUT_SECONDS"
    )

    # Sales-invoice PDF branding (logo, placement, template) — best-effort,
    # same reasoning as Transactions Service above: a company's chosen
    # look is a nice-to-have on the generated PDF, not something worth
    # blocking a download over if Settings Service is briefly unreachable.
    settings_service_url: str = Field(default="http://settings-service:8009", alias="SETTINGS_SERVICE_URL")
    settings_service_timeout_seconds: float = Field(default=10.0, alias="SETTINGS_SERVICE_TIMEOUT_SECONDS")

    # Matches AI Engine's own cap (architecture report §11) — enforced here
    # too since this is the first point a file actually arrives from a user
    # upload, not just from an already-validated connector sync.
    max_upload_size_mb: int = Field(default=20, alias="MAX_UPLOAD_SIZE_MB")

    # Must match the Auth Service's JWT_SECRET_KEY, or tokens it mints are
    # rejected here. Optional so the service still runs in header-trust dev mode.
    jwt_secret_key: str = Field(default="", alias="JWT_SECRET_KEY")

    # Dev-only fallback, identical convention to every other connector/service
    # in this codebase — refused outright when APP_ENV=production, below.
    trust_company_header: bool = Field(default=True, alias="TRUST_COMPANY_HEADER")
    default_company_id: str = Field(alias="DEFAULT_COMPANY_ID")

    def model_post_init(self, _context) -> None:
        if self.is_production:
            if self.trust_company_header:
                raise ValueError(
                    "TRUST_COMPANY_HEADER must be false when APP_ENV is production: the "
                    "X-Company-ID header is not verified, so trusting it would let any "
                    "caller read or create another company's invoices."
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
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
