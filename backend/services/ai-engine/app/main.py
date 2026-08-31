from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.ocr import router as ocr_router
from app.core.config import get_settings
from app.core.logging import configure_logging

settings = get_settings()
configure_logging(log_level=settings.log_level)

app = FastAPI(
    title="FinPilot AI Engine",
    version="0.1.0",
    description=(
        "Phase 2b (docs/invoice-ocr-plan.md): invoice OCR/structuring only. "
        "The AI Chat Assistant and AI Insights responsibilities described in "
        "the architecture report §5.8 are not implemented yet."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_base_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ocr_router, prefix="/api/v1/ai")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
