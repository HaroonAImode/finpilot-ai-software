from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.reconciliation import router as reconciliation_router
from app.api.routes.vendors import router as vendors_router
from app.core.config import get_settings
from app.core.logging import configure_logging

settings = get_settings()
configure_logging(log_level=settings.log_level)

app = FastAPI(
    title="FinPilot Vendors Service",
    version="0.1.0",
    description=(
        "Supplier directory (architecture report §5.7). Gives vendors a "
        "stable identity so invoice matching stops relying on free-text OCR "
        "output. Spend is derived from Invoice Service on read, never stored."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_base_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(reconciliation_router, prefix="/api/v1")
app.include_router(vendors_router, prefix="/api/v1")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
