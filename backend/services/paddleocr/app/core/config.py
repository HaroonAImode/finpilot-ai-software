from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = Field(default="development", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    max_upload_size_mb: int = Field(default=20, alias="MAX_UPLOAD_SIZE_MB")

    #: PP-OCRv6 by default (paddleocr==3.7.0's own default) — not pinned to
    #: an older version, since this is the model actually verified live
    #: against this project's real receipt fixtures during the migration.
    paddleocr_lang: str = Field(default="en", alias="PADDLEOCR_LANG")
    #: Disables oneDNN acceleration. Required on this project's Windows dev
    #: host — confirmed live that PP-OCRv6 + oneDNN raises
    #: `NotImplementedError: ConvertPirAttribute2RuntimeAttribute` on CPU
    #: inference (a PaddlePaddle 3.3.1 PIR-compiler/oneDNN incompatibility,
    #: not a bug in this service). Kept configurable rather than hardcoded
    #: `False`, since the Linux container environment this actually ships
    #: in has not yet been verified to hit (or not hit) the same issue —
    #: re-test there before assuming `True` is safe.
    paddleocr_enable_mkldnn: bool = Field(default=False, alias="PADDLEOCR_ENABLE_MKLDNN")
    paddleocr_device: str = Field(default="cpu", alias="PADDLEOCR_DEVICE")

    #: Model tier — "mobile" (default) or "server". Both are PP-OCRv5
    #: detection+recognition; neither is an LLM (see engine.py's docstring on
    #: why that distinction matters for this project).
    #:
    #: "mobile" is the default on measured evidence, not on assumption. Both
    #: tiers were built and run against this project's own real invoice on
    #: the development host (8 CPU / 4.8GB to Docker):
    #:
    #:   mobile  ~150-350s per page
    #:   server  ~460s through the pipeline, and a direct call to this
    #:           service timed out at 900s
    #:
    #: The accuracy difference did not justify that. server read the line
    #: items noticeably better (correct product names where mobile misread
    #: "Fusion Backpack" as the SKU above it) but simultaneously *lost* the
    #: invoice date and picked a logo glyph into the vendor name — a wash
    #: overall, for 2-3x the latency on hardware that was already the
    #: bottleneck.
    #:
    #: Keep this configurable rather than hardcoding the winner: "server" is
    #: very likely the right choice on a host with real CPU headroom, and
    #: switching is one env value. The tier is baked into the Docker image
    #: at build time (see the Dockerfile's model-baking step), so changing
    #: it needs a rebuild, not just a restart.
    paddleocr_model_tier: Literal["server", "mobile"] = Field(default="mobile", alias="PADDLEOCR_MODEL_TIER")

    @property
    def detection_model_name(self) -> str:
        return f"PP-OCRv5_{self.paddleocr_model_tier}_det"

    @property
    def recognition_model_name(self) -> str:
        return f"PP-OCRv5_{self.paddleocr_model_tier}_rec"

    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
