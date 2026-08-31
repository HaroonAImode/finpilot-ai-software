from uuid import UUID

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from shared.auth import TokenError, bearer_token_from_header, decode_access_token

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.models import EmailAccount


async def get_company_id(
    authorization: str | None = Header(default=None),
    x_company_id: str | None = Header(default=None, alias="X-Company-ID"),
    settings: Settings = Depends(get_settings),
) -> UUID:
    """Resolve which company this request belongs to.

    Identical precedence to slack-connector's get_company_id: a verified JWT
    first (the only trustworthy source), then the unverified X-Company-ID
    header only when trust_company_header is on, then DEFAULT_COMPANY_ID. Both
    fallbacks are dev-only and refused in production — see config.py's
    model_post_init. A bad token is always rejected outright rather than
    falling through to a dev path.
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


async def get_account_for_company(
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
) -> EmailAccount:
    account = await db.scalar(select(EmailAccount).where(EmailAccount.company_id == company_id))
    if account is None:
        raise HTTPException(status_code=404, detail="No email account is connected for this company")
    return account


async def get_optional_account_for_company(
    company_id: UUID = Depends(get_company_id),
    db: AsyncSession = Depends(get_db),
) -> EmailAccount | None:
    return await db.scalar(select(EmailAccount).where(EmailAccount.company_id == company_id))
