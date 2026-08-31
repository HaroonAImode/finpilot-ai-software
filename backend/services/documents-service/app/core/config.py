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

    # The send-to-scanner bridge. Same plain-HTTP, degrade-clearly pattern the
    # Slack/Email connectors' own scanner_bridge.py uses — RabbitMQ still does
    # not exist in this compose (see the architecture report's status note), so
    # nothing here assumes a queue.
    invoice_service_url: str = Field(default="http://invoice-service:8002", alias="INVOICE_SERVICE_URL")
    # Generous by default: the scan it triggers runs OCR, which on a
    # CPU-constrained host genuinely takes minutes (measured — see
    # docs/invoice-ocr-plan.md's PaddleOCR latency notes). A short timeout here
    # fails uploads that would otherwise have succeeded.
    invoice_service_timeout_seconds: float = Field(default=240.0, alias="INVOICE_SERVICE_TIMEOUT_SECONDS")

    # Same cap as Invoice Service and AI Engine (architecture report §11).
    max_upload_size_mb: int = Field(default=20, alias="MAX_UPLOAD_SIZE_MB")

    jwt_secret_key: str = Field(default="", alias="JWT_SECRET_KEY")
    trust_company_header: bool = Field(default=True, alias="TRUST_COMPANY_HEADER")
    default_company_id: str = Field(alias="DEFAULT_COMPANY_ID")

    def model_post_init(self, _context) -> None:
        if self.is_production:
            if self.trust_company_header:
                raise ValueError(
                    "TRUST_COMPANY_HEADER must be false when APP_ENV is production: the "
                    "X-Company-ID header is not verified, so trusting it would let any "
                    "caller read or delete another company's documents."
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
