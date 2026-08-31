from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.employees import router as employees_router
from app.api.routes.payroll import router as payroll_router
from app.core.config import get_settings
from app.core.logging import configure_logging

settings = get_settings()
configure_logging(log_level=settings.log_level)

app = FastAPI(
    title="FinPilot HR Service",
    version="0.1.0",
    description=(
        "Employee directory and payroll processing (architecture report "
        "§5.5). Processing a payroll run books a matching Expense in "
        "Transactions Service, best-effort."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_base_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# payroll_router first: its literal /employees/payroll* paths must be
# matched before employees_router's /{employee_id} pattern gets a chance
# to treat "payroll" as an id — the same ordering rule sales_scanner_router
# documents in invoice-service's own main.py.
app.include_router(payroll_router, prefix="/api/v1")
app.include_router(employees_router, prefix="/api/v1")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
