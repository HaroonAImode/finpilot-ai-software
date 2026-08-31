from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Signing key for access tokens. Every service that verifies a token needs
    # the same value, so it lives in the environment, never in code.
    jwt_secret_key: str = Field(alias="JWT_SECRET_KEY")
    access_token_expire_minutes: int = Field(default=15, alias="ACCESS_TOKEN_EXPIRE_MINUTES")
    refresh_token_expire_days: int = Field(default=7, alias="REFRESH_TOKEN_EXPIRE_DAYS")

    database_url: str = Field(alias="DATABASE_URL")
    redis_url: str = Field(default="redis://localhost:6379/6", alias="REDIS_URL")

    app_env: str = Field(default="development", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    frontend_base_url: str = Field(default="http://localhost:8080", alias="FRONTEND_BASE_URL")

    # Architecture report §20: rate limiting. Applied to the login endpoint,
    # which is the one an attacker can hammer without credentials.
    login_rate_limit_per_minute: int = Field(default=10, alias="LOGIN_RATE_LIMIT_PER_MINUTE")

    def model_post_init(self, _context) -> None:
        # Fail at startup rather than at the first login attempt. A too-short key
        # makes every token in the system trivially forgeable, so this is worth
        # refusing to boot over.
        if len(self.jwt_secret_key) < 32:
            raise ValueError(
                "JWT_SECRET_KEY must be at least 32 characters. Generate one with:\n"
                '  python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in {"production", "prod"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
