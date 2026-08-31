from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.orders import router as orders_router
from app.api.routes.requests import router as requests_router
from app.api.routes.stats import router as stats_router
from app.api.routes.vendor_comparison import router as vendor_comparison_router
from app.core.config import get_settings
from app.core.logging import configure_logging

settings = get_settings()
configure_logging(log_level=settings.log_level)

app = FastAPI(
    title="FinPilot Procurement Service",
    version="0.1.0",
    description=(
        "Purchase requests, purchase orders and vendor quote comparison "
        "(architecture report §5.6) — from request to delivered order."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_base_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(requests_router, prefix="/api/v1")
app.include_router(orders_router, prefix="/api/v1")
app.include_router(vendor_comparison_router, prefix="/api/v1")
app.include_router(stats_router, prefix="/api/v1")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
