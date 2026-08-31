import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from shared.base_schemas import error_response

from app.api.v1.auth import router as auth_router
from app.core.config import get_settings

settings = get_settings()
logging.basicConfig(level=settings.log_level.upper())
logger = logging.getLogger(__name__)

app = FastAPI(title="FinPilot Auth Service", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_base_url],
    # Required so the browser will send and store the httpOnly refresh cookie.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Every error leaves in the shared shape (architecture report §9)."""
    return JSONResponse(
        status_code=exc.status_code,
        content=error_response(
            error=exc.__class__.__name__.replace("Exception", "") or "Error",
            detail=str(exc.detail),
            code=exc.status_code,
        ),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(p) for p in first.get("loc", []) if p != "body")
    return JSONResponse(
        status_code=422,
        content=error_response(
            error="Validation Error",
            detail=f"{field}: {first.get('msg', 'invalid input')}" if field else "Invalid input",
            code=422,
        ),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Never leak a stack trace or database error to the client (§20)."""
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content=error_response(
            error="Internal Server Error",
            detail="Something went wrong on our side. Please try again.",
            code=500,
        ),
    )


app.include_router(auth_router, prefix="/api/v1/auth")


@app.get("/health")
async def health():
    return {"status": "ok"}
