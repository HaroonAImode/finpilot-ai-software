from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.invoices import router as invoices_router
from app.api.routes.sales import router as sales_router
from app.api.routes.sales_scanner import router as sales_scanner_router
from app.api.routes.scanner import router as scanner_router
from app.core.config import get_settings
from app.core.logging import configure_logging

settings = get_settings()
configure_logging(log_level=settings.log_level)

app = FastAPI(title="FinPilot Invoice Service", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_base_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# scanner_router first: it owns the literal /invoices/scan and
# /invoices/scan/{job_id} paths, which must be matched before
# invoices_router's /invoices/{invoice_id} pattern gets a chance to treat
# "scan" as if it were an invoice id.
app.include_router(scanner_router, prefix="/api/v1")
# Same ordering reason again, one level deeper: sales_scanner_router owns
# the literal /invoices/sales/scan, /scanned, /summary paths, which must
# match before sales_router's own /invoices/sales/{invoice_id} pattern
# treats any of them as an invoice id.
app.include_router(sales_scanner_router, prefix="/api/v1")
# Same ordering reason as scanner_router: this owns the literal
# /invoices/sales paths, which must match before invoices_router's
# /invoices/{invoice_id} pattern treats "sales" as an invoice id.
app.include_router(sales_router, prefix="/api/v1")
app.include_router(invoices_router, prefix="/api/v1")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
