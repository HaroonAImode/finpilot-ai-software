from uuid import UUID

from fastapi import Depends, Header, HTTPException
from shared.auth import TokenError, bearer_token_from_header, decode_access_token

from app.core.config import Settings, get_settings


async def get_company_id(
    authorization: str | None = Header(default=None),
    x_company_id: str | None = Header(default=None, alias="X-Company-ID"),
    settings: Settings = Depends(get_settings),
) -> UUID:
    """Resolve which company this request belongs to.

    Identical precedence to every other service in this codebase: a
    verified JWT first, then the unverified X-Company-ID header only when
    trust_company_header is on (dev only, refused in production — see
    config.py's model_post_init), then DEFAULT_COMPANY_ID. Behind the
    Gateway, X-Company-ID is written from verified JWT claims (see
    gateway/app/core/proxy.py) — this service still checks Authorization
    directly too, so it works identically whether called through the
    Gateway or directly (e.g. Invoice Service's own book-from-invoice
    caller, local dev).
    """
    if authorization:
        try:
            claims = decode_access_token(
                bearer_token_from_header(authorization), secret_key=settings.jwt_secret_key
            )
        except TokenError as exc:
            raise HTTPException(status_code=401, detail="Not authenticated") from exc
        return claims.company_id

    if not settings.trust_company_header:
        raise HTTPException(status_code=401, detail="Not authenticated")

    raw = x_company_id or settings.default_company_id
    try:
        return UUID(raw)
    except (ValueError, AttributeError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid company identifier") from exc
