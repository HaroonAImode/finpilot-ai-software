"""Auth business rules.

The negative paths carry the security properties, so they get the most cover:
no user enumeration, no reuse of a revoked session, no login to a deactivated
account, and refresh-token rotation that detects replay.
"""
import pytest

from shared.auth import decode_access_token
from shared.exceptions import ConflictError, UnauthorizedError

from app.core.security import hash_refresh_token
from app.services.auth_service import AuthService

SIGNUP = dict(
    company_name="Khan Enterprises",
    full_name="Ayesha Khan",
    email="ayesha@khan.pk",
    password="a-long-enough-passphrase",
)


async def _signup(db, settings, **overrides):
    payload = {**SIGNUP, **overrides}
    return await AuthService(db, settings).signup(**payload)


class TestSignup:
    @pytest.mark.asyncio
    async def test_creates_company_and_admin_and_returns_usable_token(self, db, settings) -> None:
        access_token, expires_in, refresh_token, user = await _signup(db, settings)

        assert user.email == "ayesha@khan.pk"
        assert user.role == "admin"
        assert user.company_name == "Khan Enterprises"
        assert refresh_token

        claims = decode_access_token(access_token, secret_key=settings.jwt_secret_key)
        assert claims.user_id == user.id
        assert claims.company_id == user.company_id
        assert 0 < expires_in <= settings.access_token_expire_minutes * 60

    @pytest.mark.asyncio
    async def test_each_signup_gets_its_own_company(self, db, settings) -> None:
        _, _, _, first = await _signup(db, settings)
        _, _, _, second = await _signup(db, settings, email="bilal@other.pk", company_name="Other Co")

        assert first.company_id != second.company_id, "tenants must not share a company"

    @pytest.mark.asyncio
    async def test_duplicate_email_is_rejected(self, db, settings) -> None:
        await _signup(db, settings)
        with pytest.raises(ConflictError):
            await _signup(db, settings, company_name="Different Co")

    @pytest.mark.asyncio
    async def test_email_is_stored_lowercased(self, db, settings) -> None:
        _, _, _, user = await _signup(db, settings, email="Ayesha@Khan.PK")
        assert user.email == "ayesha@khan.pk"

    @pytest.mark.asyncio
    async def test_password_is_never_stored_in_plain_text(self, db, settings) -> None:
        from sqlalchemy import select
        from app.models import User

        await _signup(db, settings)
        stored = await db.scalar(select(User).where(User.email == "ayesha@khan.pk"))

        assert stored.password_hash != SIGNUP["password"]
        assert SIGNUP["password"] not in stored.password_hash
        assert stored.password_hash.startswith("$argon2"), "must be an Argon2 hash"


class TestLogin:
    @pytest.mark.asyncio
    async def test_correct_password_succeeds(self, db, settings) -> None:
        await _signup(db, settings)
        access_token, _, refresh_token, user = await AuthService(db, settings).login(
            email=SIGNUP["email"], password=SIGNUP["password"]
        )
        assert access_token and refresh_token and user.email == SIGNUP["email"]

    @pytest.mark.asyncio
    async def test_wrong_password_is_rejected(self, db, settings) -> None:
        await _signup(db, settings)
        with pytest.raises(UnauthorizedError):
            await AuthService(db, settings).login(email=SIGNUP["email"], password="not-the-password")

    @pytest.mark.asyncio
    async def test_unknown_and_wrong_password_give_the_same_message(self, db, settings) -> None:
        """Otherwise the error text tells an attacker which emails are registered."""
        await _signup(db, settings)
        service = AuthService(db, settings)

        with pytest.raises(UnauthorizedError) as unknown:
            await service.login(email="nobody@nowhere.pk", password="whatever-long-pass")
        with pytest.raises(UnauthorizedError) as wrong:
            await service.login(email=SIGNUP["email"], password="wrong-but-long-pass")

        assert str(unknown.value) == str(wrong.value)

    @pytest.mark.asyncio
    async def test_login_is_case_insensitive_on_email(self, db, settings) -> None:
        await _signup(db, settings)
        _, _, _, user = await AuthService(db, settings).login(
            email="AYESHA@KHAN.PK", password=SIGNUP["password"]
        )
        assert user.email == "ayesha@khan.pk"

    @pytest.mark.asyncio
    async def test_deactivated_account_cannot_log_in(self, db, settings) -> None:
        from sqlalchemy import select
        from app.models import User

        await _signup(db, settings)
        user = await db.scalar(select(User).where(User.email == SIGNUP["email"]))
        user.is_active = False
        await db.commit()

        with pytest.raises(UnauthorizedError, match="deactivated"):
            await AuthService(db, settings).login(email=SIGNUP["email"], password=SIGNUP["password"])


class TestRefresh:
    @pytest.mark.asyncio
    async def test_valid_refresh_returns_new_tokens(self, db, settings) -> None:
        _, _, refresh_token, _ = await _signup(db, settings)
        access_token, _, new_refresh, user = await AuthService(db, settings).refresh(refresh_token)

        assert access_token
        assert new_refresh != refresh_token, "refresh tokens must rotate"
        assert user.email == SIGNUP["email"]

    @pytest.mark.asyncio
    async def test_old_token_stops_working_after_rotation(self, db, settings) -> None:
        _, _, first_token, _ = await _signup(db, settings)
        await AuthService(db, settings).refresh(first_token)

        with pytest.raises(UnauthorizedError):
            await AuthService(db, settings).refresh(first_token)

    @pytest.mark.asyncio
    async def test_replaying_a_revoked_token_kills_every_session(self, db, settings) -> None:
        """A replay means the token leaked — end all sessions, not just this one."""
        from sqlalchemy import func, select
        from app.models import RefreshToken

        _, _, first_token, _ = await _signup(db, settings)
        _, _, second_token, _ = await AuthService(db, settings).refresh(first_token)

        with pytest.raises(UnauthorizedError, match="every session"):
            await AuthService(db, settings).refresh(first_token)

        live = await db.scalar(
            select(func.count()).select_from(RefreshToken).where(RefreshToken.revoked_at.is_(None))
        )
        assert live == 0, "the still-valid token must also be revoked"

        with pytest.raises(UnauthorizedError):
            await AuthService(db, settings).refresh(second_token)

    @pytest.mark.asyncio
    async def test_unknown_and_empty_tokens_are_rejected(self, db, settings) -> None:
        service = AuthService(db, settings)
        for bad in ["", "not-a-real-token"]:
            with pytest.raises(UnauthorizedError):
                await service.refresh(bad)

    @pytest.mark.asyncio
    async def test_expired_token_is_rejected(self, db, settings) -> None:
        from datetime import datetime, timedelta, timezone
        from sqlalchemy import select
        from app.models import RefreshToken

        _, _, refresh_token, _ = await _signup(db, settings)
        stored = await db.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(refresh_token))
        )
        stored.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await db.commit()

        with pytest.raises(UnauthorizedError, match="expired"):
            await AuthService(db, settings).refresh(refresh_token)


class TestLogout:
    @pytest.mark.asyncio
    async def test_logout_revokes_the_token(self, db, settings) -> None:
        _, _, refresh_token, _ = await _signup(db, settings)
        await AuthService(db, settings).logout(refresh_token)

        with pytest.raises(UnauthorizedError):
            await AuthService(db, settings).refresh(refresh_token)

    @pytest.mark.asyncio
    async def test_logout_is_safe_with_no_or_unknown_token(self, db, settings) -> None:
        service = AuthService(db, settings)
        await service.logout(None)
        await service.logout("never-issued")


class TestRefreshTokenStorage:
    @pytest.mark.asyncio
    async def test_raw_refresh_token_is_not_stored(self, db, settings) -> None:
        """A database leak must not hand out working sessions."""
        from sqlalchemy import select
        from app.models import RefreshToken

        _, _, refresh_token, _ = await _signup(db, settings)
        rows = list(await db.scalars(select(RefreshToken)))

        assert len(rows) == 1
        assert rows[0].token_hash != refresh_token
        assert rows[0].token_hash == hash_refresh_token(refresh_token)
