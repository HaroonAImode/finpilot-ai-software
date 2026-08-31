from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Same key the Auth Service signs with. The Gateway is the only place that
    # needs to verify, once the services sit behind it.
    jwt_secret_key: str = Field(alias="JWT_SECRET_KEY")

    auth_service_url: str = Field(default="http://auth:8001", alias="AUTH_SERVICE_URL")
    slack_connector_url: str = Field(
        default="http://slack-connector:8010", alias="SLACK_CONNECTOR_URL"
    )
    email_connector_url: str = Field(
        default="http://email-connector:8011", alias="EMAIL_CONNECTOR_URL"
    )
    invoice_service_url: str = Field(
        default="http://invoice-service:8002", alias="INVOICE_SERVICE_URL"
    )
    documents_service_url: str = Field(
        default="http://documents-service:8013", alias="DOCUMENTS_SERVICE_URL"
    )
    vendors_service_url: str = Field(
        default="http://vendors-service:8006", alias="VENDORS_SERVICE_URL"
    )
    transactions_service_url: str = Field(
        default="http://transactions-service:8003", alias="TRANSACTIONS_SERVICE_URL"
    )
    hr_service_url: str = Field(
        default="http://hr-service:8004", alias="HR_SERVICE_URL"
    )
    procurement_service_url: str = Field(
        default="http://procurement-service:8005", alias="PROCUREMENT_SERVICE_URL"
    )
    settings_service_url: str = Field(
        default="http://settings-service:8009", alias="SETTINGS_SERVICE_URL"
    )
    # Architecture report §5.9 named port 8008 — since taken by PaddleOCR
    # (added later by the LiteParse+PaddleOCR migration), reassigned to
    # 8014, the next free port in this compose file.
    reports_service_url: str = Field(
        default="http://reports-service:8014", alias="REPORTS_SERVICE_URL"
    )

    redis_url: str = Field(default="redis://localhost:6379/7", alias="REDIS_URL")
    app_env: str = Field(default="development", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    frontend_base_url: str = Field(default="http://localhost:8080", alias="FRONTEND_BASE_URL")

    # Architecture report §20: 100 req/min per IP, 1000 req/min per authenticated user.
    rate_limit_per_ip: int = Field(default=100, alias="RATE_LIMIT_PER_IP")
    rate_limit_per_user: int = Field(default=1000, alias="RATE_LIMIT_PER_USER")

    # Long enough for a large file download to stream through.
    proxy_timeout_seconds: float = Field(default=120.0, alias="PROXY_TIMEOUT_SECONDS")

    def model_post_init(self, _context) -> None:
        if len(self.jwt_secret_key) < 32:
            raise ValueError(
                "JWT_SECRET_KEY must be at least 32 characters and identical to the Auth "
                "Service's. Generate one with:\n"
                '  python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in {"production", "prod"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
