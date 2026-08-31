from uuid import UUID

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from shared.auth import TokenError, bearer_token_from_header, decode_access_token

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.models import Installation


async def get_company_id(
    authorization: str | None = Header(default=None),
    x_company_id: str | None = Header(default=None, alias="X-Company-ID"),
    settings: Settings = Depends(get_settings),
) -> UUID:
    """Resolve which company this request belongs to.

    Order matters, strongest evidence first:

    1. **A verified JWT.** The Auth Service signs `company_id` into the token, so
       once the signature checks out the claim cannot have been chosen by the
       caller. This is the only trustworthy source.
    2. **The X-Company-ID header**, accepted only when `trust_company_header` is
       on. Architecture report §19 has the Gateway verify the JWT and inject this
       header for downstream services — but until that Gateway exists, nothing
       stops a caller setting the header themselves and reading another
       company's documents. It is therefore off unless explicitly enabled, and
       refused outright in production.
    3. **DEFAULT_COMPANY_ID**, same restriction — a local-development
       convenience so the service is usable before signing in.

    A bad token is always rejected rather than quietly falling through to the
    dev paths: sending a broken token means you meant to authenticate.
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


async def get_installation_for_company(
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
) -> Installation:
    installation = await db.scalar(select(Installation).where(Installation.company_id == company_id))
    if installation is None:
        raise HTTPException(status_code=404, detail="Slack is not connected for this company")
    return installation


async def get_optional_installation_for_company(
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
) -> Installation | None:
    return await db.scalar(select(Installation).where(Installation.company_id == company_id))
