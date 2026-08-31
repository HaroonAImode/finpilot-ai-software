from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.reports import router as reports_router
from app.core.config import get_settings
from app.core.logging import configure_logging

settings = get_settings()
configure_logging(log_level=settings.log_level)

app = FastAPI(
    title="FinPilot Reports Service",
    version="0.1.0",
    description=(
        "Profit & Loss, Cash Flow, Tax Summary, Sales, Purchase, and "
        "(simplified) Balance Sheet reports (architecture report §5.9), "
        "computed synchronously from Invoice/Transactions/Settings Service "
        "data and rendered as PDF or Excel on demand."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_base_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(reports_router, prefix="/api/v1")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
