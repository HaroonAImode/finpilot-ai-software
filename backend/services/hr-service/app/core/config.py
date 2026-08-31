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

    # Payroll processing books a matching Expense in Transactions Service
    # (§5.4), best-effort — same reasoning as Invoice Service's own
    # send-to-accounting hook: no queue exists yet (architecture report
    # §11b), so a failure here is logged and does not block payroll from
    # being recorded in this service's own PayrollRecord history.
    transactions_service_url: str = Field(
        default="http://transactions-service:8003", alias="TRANSACTIONS_SERVICE_URL"
    )
    transactions_service_timeout_seconds: float = Field(
        default=10.0, alias="TRANSACTIONS_SERVICE_TIMEOUT_SECONDS"
    )

    jwt_secret_key: str = Field(default="", alias="JWT_SECRET_KEY")
    trust_company_header: bool = Field(default=True, alias="TRUST_COMPANY_HEADER")
    default_company_id: str = Field(alias="DEFAULT_COMPANY_ID")

    def model_post_init(self, _context) -> None:
        if self.is_production:
            if self.trust_company_header:
                raise ValueError(
                    "TRUST_COMPANY_HEADER must be false when APP_ENV is production: the "
                    "X-Company-ID header is not verified, so trusting it would let any "
                    "caller read or edit another company's employees."
                )
            if not self.jwt_secret_key:
                raise ValueError(
                    "JWT_SECRET_KEY is required in production — it is the only way to "
                    "verify which company a request belongs to."
                )

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in {"production", "prod"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
