from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.documents import router as documents_router
from app.core.config import get_settings
from app.core.logging import configure_logging

settings = get_settings()
configure_logging(log_level=settings.log_level)

app = FastAPI(
    title="FinPilot Documents Service",
    version="0.1.0",
    description=(
        "The browser-upload document library — the fourth source on the "
        "Documents page, alongside the Slack, Email and WhatsApp connectors. "
        "Stores what a user uploads so it persists as a document in its own "
        "right, rather than only surviving as an extracted invoice."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_base_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(documents_router, prefix="/api/v1")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
