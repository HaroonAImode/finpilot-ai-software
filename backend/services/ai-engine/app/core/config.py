from functools import lru_cache
from typing import Literal, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = Field(default="development", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    frontend_base_url: str = Field(default="http://localhost:8080", alias="FRONTEND_BASE_URL")

    # LiteParse+PaddleOCR migration (docs/invoice-ocr-plan.md) — "liteparse"
    # is the new default primary path (native text -> PaddleOCR when
    # configured -> LiteParse's own built-in OCR); "tesseract" is the full,
    # unmodified legacy PyMuPDF+pytesseract pipeline this project already
    # had, kept as an explicit one-variable rollback switch, never removed.
    ocr_engine: Literal["liteparse", "tesseract"] = Field(default="liteparse", alias="OCR_ENGINE")
    #: Internal URL of the PaddleOCR microservice, passed to LiteParse as
    #: its external OCR server (LiteParse's own documented OCR_API_SPEC.md
    #: contract). None (unset) means "PaddleOCR not configured" — the
    #: liteparse path then falls back to LiteParse's own built-in OCR
    #: rather than failing, exactly like a PaddleOCR-service outage would.
    paddleocr_service_url: Optional[str] = Field(default=None, alias="PADDLEOCR_SERVICE_URL")
    ocr_language: str = Field(default="en", alias="OCR_LANGUAGE")

    # docs/invoice-ocr-plan.md's architecture report reference (§11) caps
    # invoice uploads at 20MB — enforced here too since this endpoint does
    # its own file-format parsing (PyMuPDF/Tesseract) before Invoice Service
    # (Phase 3) exists to have already validated size upstream.
    max_upload_size_mb: int = Field(default=20, alias="MAX_UPLOAD_SIZE_MB")

    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
