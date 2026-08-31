from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.auth import router as auth_router
from app.api.routes.sync import router as sync_router, files_router, conversations_router
from app.core.config import assert_usable_encryption_key, get_settings
from app.core.logging import configure_logging

settings = get_settings()
configure_logging(log_level=settings.log_level)

# Refuse to start on a key we cannot encrypt tokens with, rather than serving
# traffic and failing with a 500 the first time someone connects a workspace.
assert_usable_encryption_key(settings.token_encryption_key)

app = FastAPI(title="FinPilot Slack Connector", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_base_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix="/api/v1/slack")
app.include_router(sync_router, prefix="/api/v1/slack")
app.include_router(files_router, prefix="/api/v1/slack")
app.include_router(conversations_router, prefix="/api/v1/slack")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
