from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.expenses import router as expenses_router
from app.api.routes.kpis import router as kpis_router
from app.core.config import get_settings
from app.core.logging import configure_logging

settings = get_settings()
configure_logging(log_level=settings.log_level)

app = FastAPI(
    title="FinPilot Transactions Service",
    version="0.1.0",
    description=(
        "Expense ledger and Dashboard KPIs (architecture report §5.4). "
        "Revenue is deliberately not a second table here — it stays "
        "Invoice Service's own scanned-sales ledger (Revenue Manager); see "
        "docs/superpowers/specs/2026-08-29-transactions-service-design.md."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_base_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(expenses_router, prefix="/api/v1")
app.include_router(kpis_router, prefix="/api/v1")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
