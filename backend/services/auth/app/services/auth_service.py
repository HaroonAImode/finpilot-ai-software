"""Authentication business rules — the security decisions live here."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from shared.auth import create_access_token
from shared.exceptions import ConflictError, UnauthorizedError

from app.core.config import Settings
from app.core.security import (
    generate_refresh_token, hash_password, hash_refresh_token, password_needs_rehash, verify_password,
)
from app.models import User
from app.repositories.user_repository import UserRepository
from app.schemas.auth import UserResponse

# One message for every login failure. Saying "no such account" would let anyone
# enumerate which email addresses are registered.
INVALID_CREDENTIALS = "Email or password is incorrect"


def _as_utc(value: datetime) -> datetime:
    """Normalise a stored timestamp to timezone-aware UTC before comparing.

    asyncpg hands back aware datetimes but SQLite (used by the tests) hands back
    naive ones, and comparing the two raises TypeError. Without this, expiry
    checks would work on Postgres and crash anywhere else — the kind of gap that
    only shows up in whichever environment you test least.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


class AuthService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.repo = UserRepository(db)

    # --- helpers ---

    def _user_response(self, user: User) -> UserResponse:
        return UserResponse(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            role=user.role,
            company_id=user.company_id,
            company_name=user.company.name,
            created_at=user.created_at,
        )

    def _issue_access_token(self, user: User) -> tuple[str, int]:
        return create_access_token(
            secret_key=self.settings.jwt_secret_key,
            user_id=user.id,
            company_id=user.company_id,
            role=user.role,
            email=user.email,
            expires_minutes=self.settings.access_token_expire_minutes,
        )

    async def _issue_refresh_token(self, user: User) -> str:
        raw = generate_refresh_token()
        await self.repo.store_refresh_token(
            user_id=user.id,
            token_hash=hash_refresh_token(raw),
            expires_at=datetime.now(timezone.utc) + timedelta(days=self.settings.refresh_token_expire_days),
        )
        return raw

    # --- use cases ---

    async def signup(self, *, company_name: str, full_name: str, email: str, password: str):
        if await self.repo.email_exists(email):
            raise ConflictError("An account with this email already exists")

        user = await self.repo.create_company_with_admin(
            company_name=company_name,
            full_name=full_name,
            email=email,
            password_hash=hash_password(password),
        )

        try:
            access_token, expires_in = self._issue_access_token(user)
            refresh_token = await self._issue_refresh_token(user)
            await self.db.commit()
        except IntegrityError as exc:
            # Two simultaneous signups with the same email both pass the check
            # above; the unique index is what actually decides. Report it as the
            # same conflict rather than a 500.
            await self.db.rollback()
            raise ConflictError("An account with this email already exists") from exc

        return access_token, expires_in, refresh_token, self._user_response(user)

    async def login(self, *, email: str, password: str):
        user = await self.repo.get_user_by_email(email)

        if user is None:
            # Hash anyway so a missing account and a wrong password take a
            # similar amount of time; skipping it makes user enumeration
            # measurable with a stopwatch.
            hash_password(password)
            raise UnauthorizedError(INVALID_CREDENTIALS)

        if not verify_password(password, user.password_hash):
            raise UnauthorizedError(INVALID_CREDENTIALS)

        if not user.is_active:
            raise UnauthorizedError("This account has been deactivated")

        # Opportunistically upgrade hashes written under older Argon2 parameters.
        if password_needs_rehash(user.password_hash):
            await self.repo.update_password_hash(user, hash_password(password))

        await self.repo.touch_last_login(user)
        access_token, expires_in = self._issue_access_token(user)
        refresh_token = await self._issue_refresh_token(user)
        await self.db.commit()

        return access_token, expires_in, refresh_token, self._user_response(user)

    async def refresh(self, raw_refresh_token: str):
        """Exchange a refresh token for a new access token, rotating the refresh token.

        Rotation matters: if a stolen token is used after the real user has
        already refreshed, the replay lands on a row that is revoked. That is a
        strong signal the session leaked, so every session for that user is
        revoked and they must log in again.
        """
        if not raw_refresh_token:
            raise UnauthorizedError("No refresh token supplied")

        stored = await self.repo.get_refresh_token(hash_refresh_token(raw_refresh_token))
        if stored is None:
            raise UnauthorizedError("Refresh token is not valid")

        now = datetime.now(timezone.utc)
        if stored.revoked_at is not None:
            revoked = await self.repo.revoke_all_for_user(stored.user_id)
            await self.db.commit()
            raise UnauthorizedError(
                "This session was already ended. For your security every session has been "
                f"signed out ({revoked} revoked) — please log in again."
            )

        if _as_utc(stored.expires_at) <= now:
            raise UnauthorizedError("Session has expired — please log in again")

        user = stored.user
        if not user.is_active:
            raise UnauthorizedError("This account has been deactivated")

        await self.repo.revoke_refresh_token(stored)
        access_token, expires_in = self._issue_access_token(user)
        new_refresh = await self._issue_refresh_token(user)
        await self.db.commit()

        return access_token, expires_in, new_refresh, self._user_response(user)

    async def logout(self, raw_refresh_token: str | None) -> None:
        """Always succeeds — logging out must never fail or reveal token validity."""
        if not raw_refresh_token:
            return
        stored = await self.repo.get_refresh_token(hash_refresh_token(raw_refresh_token))
        if stored is not None:
            await self.repo.revoke_refresh_token(stored)
            await self.db.commit()
