from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from shared.auth import TokenError, bearer_token_from_header, decode_access_token
from shared.exceptions import ConflictError, RateLimitedError, UnauthorizedError

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.rate_limit import login_rate_limiter
from app.repositories.user_repository import UserRepository
from app.schemas.auth import LoginRequest, SignupRequest, TokenResponse, UserResponse
from app.services.auth_service import AuthService

router = APIRouter(tags=["auth"])

REFRESH_COOKIE = "finpilot_refresh"


def _set_refresh_cookie(response: Response, token: str, settings: Settings) -> None:
    """httpOnly so page scripts (and any XSS) cannot read it — architecture §19."""
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=token,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        max_age=settings.refresh_token_expire_days * 24 * 60 * 60,
        path="/api/v1/auth",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(REFRESH_COOKIE, path="/api/v1/auth")


@router.post("/signup", response_model=TokenResponse, status_code=201)
async def signup(
    payload: SignupRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Register a new company and its first admin user."""
    service = AuthService(db, settings)
    try:
        access_token, expires_in, refresh_token, user = await service.signup(
            company_name=payload.company_name,
            full_name=payload.full_name,
            email=payload.email,
            password=payload.password,
        )
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    _set_refresh_cookie(response, refresh_token, settings)
    return TokenResponse(access_token=access_token, expires_in=expires_in, user=user)


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    # Rate limit before touching the database: this is the endpoint an attacker
    # can hammer without any credentials at all.
    try:
        await login_rate_limiter.check(request, settings)
    except RateLimitedError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    service = AuthService(db, settings)
    try:
        access_token, expires_in, refresh_token, user = await service.login(
            email=payload.email, password=payload.password
        )
    except UnauthorizedError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    _set_refresh_cookie(response, refresh_token, settings)
    return TokenResponse(access_token=access_token, expires_in=expires_in, user=user)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    service = AuthService(db, settings)
    try:
        access_token, expires_in, new_refresh, user = await service.refresh(
            request.cookies.get(REFRESH_COOKIE, "")
        )
    except UnauthorizedError as exc:
        _clear_refresh_cookie(response)
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    _set_refresh_cookie(response, new_refresh, settings)
    return TokenResponse(access_token=access_token, expires_in=expires_in, user=user)


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    await AuthService(db, settings).logout(request.cookies.get(REFRESH_COOKIE))
    _clear_refresh_cookie(response)
    return Response(status_code=204)


@router.get("/me", response_model=UserResponse)
async def me(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Who the bearer token belongs to — used by the frontend to restore a session."""
    try:
        claims = decode_access_token(
            bearer_token_from_header(authorization), secret_key=settings.jwt_secret_key
        )
    except TokenError as exc:
        raise HTTPException(status_code=401, detail="Not authenticated") from exc

    user = await UserRepository(db).get_user_by_id(claims.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Not authenticated")

    return UserResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        company_id=user.company_id,
        company_name=user.company.name,
        created_at=user.created_at,
    )
