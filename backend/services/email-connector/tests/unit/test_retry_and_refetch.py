"""Retry a failed attachment download, and the shared Gmail re-fetch it (and
approve) rely on — plan §14/Phase 4, the email counterpart of Slack's
POST /files/{id}/retry, adapted for Gmail's expiring ids (§5a.1): there is no
stable download URL to re-issue, so this goes back to the message itself.
"""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.tenancy import get_account_for_company
from app.db.session import get_db
from app.main import app
from app.models import EmailAccount, EmailAttachment, EmailMessage, EmailProviderName, ReviewStatus
from app.services.attachment_fetch import AttachmentFetchError, refetch_and_store


def _account(**overrides) -> EmailAccount:
    defaults = dict(
        company_id=uuid.uuid4(), provider=EmailProviderName.gmail, email_address="a@b.com",
        access_token_encrypted="x", refresh_token_encrypted="y",
        token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1), scopes=[],
    )
    defaults.update(overrides)
    return EmailAccount(**defaults)


@pytest.fixture
def db_session_factory():
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models.base import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.get_event_loop().run_until_complete(_setup())
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def client(db_session_factory):
    async def _db():
        async with db_session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _db
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def seeded(db_session_factory):
    import asyncio

    async def _seed():
        async with db_session_factory() as db:
            account = _account()
            db.add(account)
            await db.flush()
            message = EmailMessage(
                account_id=account.id, provider_message_id="MSG1",
                from_address="vendor@company.com", has_attachments=True,
            )
            db.add(message)
            await db.flush()
            failed = EmailAttachment(
                account_id=account.id, message_id=message.id, attachment_index=0,
                provider_attachment_id="stale-id", filename="invoice.pdf", size=5000,
                downloaded=False, download_failed=True, download_error="previous failure",
                review_status=ReviewStatus.imported,
            )
            already_done = EmailAttachment(
                account_id=account.id, message_id=message.id, attachment_index=1,
                provider_attachment_id="stale-id-2", filename="done.pdf", size=5000,
                downloaded=True, s3_key="x/done.pdf", sha256_hash="abc",
                review_status=ReviewStatus.imported,
            )
            pending_review = EmailAttachment(
                account_id=account.id, message_id=message.id, attachment_index=2,
                provider_attachment_id="stale-id-3", filename="maybe.pdf", size=5000,
                downloaded=False, review_status=ReviewStatus.needs_review, review_reason="low_confidence",
            )
            db.add_all([failed, already_done, pending_review])
            await db.commit()
            await db.refresh(failed)
            await db.refresh(already_done)
            await db.refresh(pending_review)
            return account, message, failed, already_done, pending_review

    return asyncio.get_event_loop().run_until_complete(_seed())


class TestRetryEndpoint:
    def test_retrying_an_already_downloaded_attachment_is_a_no_op(self, client, seeded) -> None:
        """Matches Slack's retry semantics: the caller's UI state was simply
        stale, not an error."""
        account, message, failed, already_done, pending_review = seeded
        app.dependency_overrides[get_account_for_company] = lambda: account

        response = client.post(f"/api/v1/email/files/{already_done.id}/retry")

        assert response.status_code == 200
        assert response.json()["downloaded"] is True

    def test_retrying_a_needs_review_attachment_is_rejected(self, client, seeded) -> None:
        """Approve exists for this path — retry is only for a genuine
        download failure on an already-decided attachment."""
        account, message, failed, already_done, pending_review = seeded
        app.dependency_overrides[get_account_for_company] = lambda: account

        response = client.post(f"/api/v1/email/files/{pending_review.id}/retry")

        assert response.status_code == 409

    def test_a_successful_retry_downloads_and_clears_the_failure(self, client, seeded) -> None:
        account, message, failed, already_done, pending_review = seeded
        app.dependency_overrides[get_account_for_company] = lambda: account

        async def _fake_refetch(settings, acc, msg, attachment):
            attachment.downloaded = True
            attachment.download_failed = False
            attachment.download_error = None
            attachment.s3_key = "new/key.pdf"
            attachment.sha256_hash = "newhash"
            attachment.provider_attachment_id = "fresh-id"

        with patch("app.api.routes.sync.refetch_and_store", side_effect=_fake_refetch):
            response = client.post(f"/api/v1/email/files/{failed.id}/retry")

        assert response.status_code == 200
        body = response.json()
        assert body["downloaded"] is True
        assert body["download_failed"] is False

    def test_a_retry_that_fails_again_records_the_new_error(self, client, seeded) -> None:
        account, message, failed, already_done, pending_review = seeded
        app.dependency_overrides[get_account_for_company] = lambda: account

        with patch(
            "app.api.routes.sync.refetch_and_store",
            side_effect=AttachmentFetchError("Gmail is unreachable right now"),
        ):
            response = client.post(f"/api/v1/email/files/{failed.id}/retry")

        assert response.status_code == 502

    def test_another_accounts_attachment_cannot_be_retried(self, client, seeded) -> None:
        account, message, failed, already_done, pending_review = seeded
        other_account = _account()
        app.dependency_overrides[get_account_for_company] = lambda: other_account

        response = client.post(f"/api/v1/email/files/{failed.id}/retry")

        assert response.status_code == 404


class TestRefetchAndStore:
    @pytest.mark.asyncio
    async def test_an_index_past_the_current_attachment_count_is_a_clean_error(self) -> None:
        """The message changed shape since it was first synced (unlikely,
        since messages are immutable — but the check must exist, not assume)."""
        account = _account()
        message = EmailMessage(account_id=account.id, provider_message_id="MSG1")
        attachment = EmailAttachment(
            account_id=account.id, message_id=uuid.uuid4(), attachment_index=5,
            filename="x.pdf", size=100, provider_attachment_id="old",
        )

        fake_gmail = AsyncMock()
        fake_gmail.get_message = AsyncMock(return_value={"payload": {"parts": []}})
        fake_gmail_cm = MagicMock()
        fake_gmail_cm.__aenter__ = AsyncMock(return_value=fake_gmail)
        fake_gmail_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("app.services.attachment_fetch.get_valid_access_token", AsyncMock(return_value="tok")), \
             patch("app.services.attachment_fetch.GmailClient", return_value=fake_gmail_cm):
            with pytest.raises(AttachmentFetchError) as exc_info:
                await refetch_and_store(MagicMock(), account, message, attachment)

        assert exc_info.value.status_code == 502
        assert "no longer present" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_a_token_refresh_failure_surfaces_as_a_clean_502(self) -> None:
        from app.services.providers.token_manager import TokenRefreshError

        account = _account()
        message = EmailMessage(account_id=account.id, provider_message_id="MSG1")
        attachment = EmailAttachment(
            account_id=account.id, message_id=uuid.uuid4(), attachment_index=0,
            filename="x.pdf", size=100, provider_attachment_id="old",
        )

        with patch(
            "app.services.attachment_fetch.get_valid_access_token",
            AsyncMock(side_effect=TokenRefreshError("refresh token is dead")),
        ):
            with pytest.raises(AttachmentFetchError) as exc_info:
                await refetch_and_store(MagicMock(), account, message, attachment)

        assert exc_info.value.status_code == 502
        assert "refresh token is dead" in exc_info.value.detail
