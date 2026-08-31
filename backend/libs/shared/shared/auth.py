"""JWT creation and verification — one implementation, used by every service.

The Auth Service mints tokens; the Gateway (and, until it exists, each service)
verifies them. Both sides live here so the algorithm, claim names and clock-skew
rules can never drift apart between services.

Deliberately has no database, no FastAPI and no service-specific imports: this
package is installed by everything, so anything heavier would couple all the
services together.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt

ALGORITHM = "HS256"

# Architecture report §20: 15-minute access tokens, 7-day refresh tokens.
ACCESS_TOKEN_EXPIRE_MINUTES = 15
REFRESH_TOKEN_EXPIRE_DAYS = 7


class TokenError(Exception):
    """Raised when a token is missing, malformed, expired or fails verification.

    Deliberately one type for every failure mode: callers turn this into a 401
    and must not tell a caller *why* verification failed, which would help an
    attacker distinguish "expired" from "bad signature" from "wrong audience".
    """


@dataclass(frozen=True)
class TokenClaims:
    """The verified identity of the caller."""

    user_id: uuid.UUID
    company_id: uuid.UUID
    role: str
    email: str
    expires_at: datetime


def create_access_token(
    *,
    secret_key: str,
    user_id: uuid.UUID,
    company_id: uuid.UUID,
    role: str,
    email: str,
    expires_minutes: int = ACCESS_TOKEN_EXPIRE_MINUTES,
) -> tuple[str, int]:
    """Mint a signed access token. Returns (token, expires_in_seconds)."""
    if not secret_key:
        raise ValueError("JWT_SECRET_KEY is empty — refusing to sign a token with no secret")

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=expires_minutes)
    payload = {
        "sub": str(user_id),
        "company_id": str(company_id),
        "role": role,
        "email": email,
        "iat": now,
        "exp": expires_at,
        # A unique id per token, so a future revocation list can name one token
        # without invalidating every token that user holds.
        "jti": str(uuid.uuid4()),
        "typ": "access",
    }
    token = jwt.encode(payload, secret_key, algorithm=ALGORITHM)
    return token, int((expires_at - now).total_seconds())


def decode_access_token(token: str, *, secret_key: str) -> TokenClaims:
    """Verify a token's signature and expiry, returning its claims.

    Raises TokenError for every failure. `algorithms` is pinned to a single
    value: accepting the token's own `alg` header is how the classic "alg: none"
    and RS256->HS256 confusion attacks get in.
    """
    if not secret_key:
        raise TokenError("No verification key configured")
    if not token:
        raise TokenError("No token supplied")

    try:
        payload = jwt.decode(
            token,
            secret_key,
            algorithms=[ALGORITHM],
            options={"require": ["exp", "sub", "company_id"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("Token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("Token is not valid") from exc

    if payload.get("typ") != "access":
        # A refresh token must never be accepted as an access token: they have
        # very different lifetimes and revocation semantics.
        raise TokenError("Token is not an access token")

    try:
        return TokenClaims(
            user_id=uuid.UUID(payload["sub"]),
            company_id=uuid.UUID(payload["company_id"]),
            role=payload.get("role", "member"),
            email=payload.get("email", ""),
            expires_at=datetime.fromtimestamp(payload["exp"], tz=timezone.utc),
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise TokenError("Token claims are malformed") from exc


def bearer_token_from_header(authorization: str | None) -> str:
    """Pull the token out of an `Authorization: Bearer <token>` header."""
    if not authorization:
        raise TokenError("Authorization header missing")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise TokenError("Authorization header must be 'Bearer <token>'")
    return token.strip()


#: A service-to-service token is short-lived by design: it is minted for one
#: internal call and discarded. It never reaches a browser and is never stored.
INTERNAL_TOKEN_EXPIRE_MINUTES = 5

#: `sub` for a service-initiated call. A fixed, non-user UUID so an audit trail
#: can tell "a service did this" apart from "a person did this" — rather than
#: attributing an automated hand-off to whichever user happened to trigger it.
SERVICE_ACTOR_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


def internal_service_headers(
    *, secret_key: str, company_id: uuid.UUID, service_name: str,
) -> dict[str, str]:
    """Auth headers for one service calling another on a company's behalf.

    Why this exists, and why `X-Company-ID` alone is not enough: services run
    with `TRUST_COMPANY_HEADER=false` in Docker — correctly, because behind the
    Gateway that header is written from *verified* JWT claims, so trusting a raw
    one would let any caller name any company. A server-to-server call has no
    end-user `Authorization` header to forward, so sending only the header gets
    a flat 401.

    That was a real, live-confirmed bug: every connector's "send to scanner"
    failed this way against the real Docker stack, while their unit tests
    passed throughout because those patch the bridge out.

    Every service already shares `JWT_SECRET_KEY`, so the honest fix is to mint
    a real token rather than ask the callee to trust an unverified header.
    `X-Company-ID` is still sent alongside it for services running in
    header-trust dev mode.
    """
    if not secret_key:
        raise ValueError(
            f"JWT_SECRET_KEY is empty — {service_name} cannot authenticate its internal calls"
        )
    token, _ = create_access_token(
        secret_key=secret_key,
        user_id=SERVICE_ACTOR_ID,
        company_id=company_id,
        role="service",
        email=f"{service_name}@internal",
        expires_minutes=INTERNAL_TOKEN_EXPIRE_MINUTES,
    )
    return {"Authorization": f"Bearer {token}", "X-Company-ID": str(company_id)}
