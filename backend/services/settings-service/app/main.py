from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.automation import router as automation_router
from app.api.routes.company import router as company_router
from app.api.routes.tax import router as tax_router
from app.core.config import get_settings
from app.core.logging import configure_logging

settings = get_settings()
configure_logging(log_level=settings.log_level)

app = FastAPI(
    title="FinPilot Settings Service",
    version="0.1.0",
    description=(
        "Company profile, tax configuration, and AI automation toggles "
        "(architecture report §5.10)."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_base_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(company_router, prefix="/api/v1")
app.include_router(automation_router, prefix="/api/v1")
app.include_router(tax_router, prefix="/api/v1")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
