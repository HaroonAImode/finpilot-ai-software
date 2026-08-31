"""Database access only — no business rules live here (architecture report §7)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Company, RefreshToken, User


class UserRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_user_by_email(self, email: str) -> User | None:
        return await self.db.scalar(
            select(User).where(User.email == email.strip().lower()).options(selectinload(User.company))
        )

    async def get_user_by_id(self, user_id: uuid.UUID) -> User | None:
        return await self.db.scalar(
            select(User).where(User.id == user_id).options(selectinload(User.company))
        )

    async def email_exists(self, email: str) -> bool:
        return await self.db.scalar(select(User.id).where(User.email == email.strip().lower())) is not None

    async def create_company_with_admin(
        self, *, company_name: str, full_name: str, email: str, password_hash: str
    ) -> User:
        company = Company(name=company_name)
        self.db.add(company)
        await self.db.flush()

        user = User(
            company_id=company.id,
            email=email.strip().lower(),
            password_hash=password_hash,
            full_name=full_name,
            role="admin",
        )
        self.db.add(user)
        await self.db.flush()
        await self.db.refresh(user, ["company"])
        return user

    async def touch_last_login(self, user: User) -> None:
        user.last_login_at = datetime.now(timezone.utc)

    async def update_password_hash(self, user: User, password_hash: str) -> None:
        user.password_hash = password_hash

    # --- refresh tokens ---

    async def store_refresh_token(
        self, *, user_id: uuid.UUID, token_hash: str, expires_at: datetime
    ) -> RefreshToken:
        token = RefreshToken(user_id=user_id, token_hash=token_hash, expires_at=expires_at)
        self.db.add(token)
        await self.db.flush()
        return token

    async def get_refresh_token(self, token_hash: str) -> RefreshToken | None:
        return await self.db.scalar(
            select(RefreshToken)
            .where(RefreshToken.token_hash == token_hash)
            .options(selectinload(RefreshToken.user).selectinload(User.company))
        )

    async def revoke_refresh_token(self, token: RefreshToken) -> None:
        if token.revoked_at is None:
            token.revoked_at = datetime.now(timezone.utc)

    async def revoke_all_for_user(self, user_id: uuid.UUID) -> int:
        """Used when a replayed token suggests the user's session was stolen."""
        tokens = await self.db.scalars(
            select(RefreshToken).where(
                RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
            )
        )
        now = datetime.now(timezone.utc)
        count = 0
        for token in tokens:
            token.revoked_at = now
            count += 1
        return count
