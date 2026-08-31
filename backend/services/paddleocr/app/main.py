from fastapi import FastAPI

from app.api.routes.ocr import router as ocr_router
from app.core.config import get_settings
from app.core.logging import configure_logging

settings = get_settings()
configure_logging(log_level=settings.log_level)

app = FastAPI(
    title="FinPilot PaddleOCR Service",
    version="0.1.0",
    description=(
        "The primary OCR engine for the LiteParse+PaddleOCR migration "
        "(docs/invoice-ocr-plan.md). Implements LiteParse's own documented "
        "OCR_API_SPEC.md external-OCR-server contract — internal-only, not "
        "registered through the Gateway, called directly by ai-engine's "
        "LiteParse client the same way every other internal service call "
        "in this codebase works."
    ),
)

app.include_router(ocr_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
