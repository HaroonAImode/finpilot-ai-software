import logging
import uuid
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from shared.auth import TokenError, bearer_token_from_header, decode_access_token
from shared.base_schemas import error_response

from app.core.config import get_settings
from app.core.proxy import build_client_headers, build_upstream_headers
from app.core.rate_limit import rate_limiter
from app.core.routing import match_route, requires_auth

settings = get_settings()
logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("gateway")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # One pooled client for the process. Creating a client per request would
    # open a new connection every time and exhaust ports under load.
    app.state.client = httpx.AsyncClient(timeout=settings.proxy_timeout_seconds, follow_redirects=False)
    yield
    await app.state.client.aclose()


app = FastAPI(title="FinPilot API Gateway", version="0.1.0", lifespan=lifespan)

# CORS is handled here rather than in each service: the browser only ever talks
# to the Gateway once it is the front door.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_base_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _error(status: int, error: str, detail: str, trace_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content=error_response(error=error, detail=detail, code=status, trace_id=trace_id),
        headers={"x-trace-id": trace_id},
    )


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.api_route(
    "/{full_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def gateway(request: Request, full_path: str):
    """The single front door: verify once, then route.

    Every request is tagged with a trace_id that is logged here, forwarded
    upstream and returned to the client, so one identifier follows a request
    across all the services (§5.1).
    """
    trace_id = str(uuid.uuid4())
    path = "/" + full_path

    route = match_route(path, settings)
    if route is None:
        return _error(404, "Not Found", f"No service handles {path}", trace_id)

    claims = None
    if requires_auth(route, path):
        try:
            claims = decode_access_token(
                bearer_token_from_header(request.headers.get("authorization")),
                secret_key=settings.jwt_secret_key,
            )
        except TokenError:
            # Never say *why* — distinguishing "expired" from "bad signature"
            # helps an attacker probe.
            logger.info("trace=%s 401 unauthenticated %s %s", trace_id, request.method, path)
            return _error(401, "Unauthorized", "Not authenticated", trace_id)

    client_ip = request.client.host if request.client else "unknown"
    if await rate_limiter.check(
        settings=settings,
        client_ip=client_ip,
        user_id=str(claims.user_id) if claims else None,
    ):
        logger.warning("trace=%s 429 rate limited %s", trace_id, client_ip)
        return _error(429, "Too Many Requests", "Slow down and try again shortly.", trace_id)

    upstream_url = f"{route.upstream.rstrip('/')}{path}"
    upstream_request = request.app.state.client.build_request(
        request.method,
        upstream_url,
        headers=build_upstream_headers(request.headers.raw, claims=claims, trace_id=trace_id),
        params=dict(request.query_params),
        content=request.stream(),
    )

    try:
        # Streamed rather than read whole: file downloads pass through the
        # Gateway, and buffering a large PDF in memory per request does not scale.
        upstream_response = await request.app.state.client.send(upstream_request, stream=True)
    except httpx.TimeoutException:
        logger.warning("trace=%s 504 upstream timeout %s", trace_id, upstream_url)
        return _error(504, "Gateway Timeout", "The service took too long to respond.", trace_id)
    except httpx.RequestError as exc:
        logger.warning("trace=%s 502 upstream unreachable %s (%s)", trace_id, upstream_url, exc)
        return _error(
            502, "Bad Gateway", "The service handling this request is unavailable.", trace_id
        )

    logger.info(
        "trace=%s %s %s -> %s %s",
        trace_id, request.method, path, upstream_response.status_code, route.upstream,
    )

    async def body():
        try:
            async for chunk in upstream_response.aiter_raw():
                yield chunk
        finally:
            await upstream_response.aclose()

    return StreamingResponse(
        body(),
        status_code=upstream_response.status_code,
        headers=build_client_headers(upstream_response.headers, trace_id=trace_id),
    )
