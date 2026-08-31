"""POST /files/{id}/send-to-scanner's `target` param — mirrors
slack-connector's own test_send_to_scanner_target.py."""
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.tenancy import get_account_for_company
from app.db.session import get_db
from app.main import app
from app.models import EmailAccount, EmailAttachment, EmailMessage, EmailProviderName


def _account() -> EmailAccount:
    return EmailAccount(
        company_id=uuid.uuid4(), provider=EmailProviderName.gmail,
        email_address="a@b.com", access_token_encrypted="x", refresh_token_encrypted="y",
        token_expires_at=datetime.now(timezone.utc), scopes=[],
    )


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
                account_id=account.id, provider_message_id="MSG1", subject="Receipt", has_attachments=True,
            )
            db.add(message)
            await db.flush()
            attachment = EmailAttachment(
                account_id=account.id, message_id=message.id, provider_attachment_id="A1",
                filename="receipt.jpg", mimetype="image/jpeg", size=100, downloaded=True, s3_key="A1/receipt.jpg",
            )
            db.add(attachment)
            await db.commit()
            return account, attachment

    return asyncio.get_event_loop().run_until_complete(_seed())


class TestTargetParam:
    def test_default_target_is_purchase(self, client, seeded) -> None:
        account, attachment = seeded
        app.dependency_overrides[get_account_for_company] = lambda: account

        with patch(
            "app.api.routes.sync.send_attachment_to_scanner",
            AsyncMock(return_value={"job_id": "j1", "status": "done"}),
        ) as mock_send:
            response = client.post(f"/api/v1/email/files/{attachment.id}/send-to-scanner")

        assert response.status_code == 200
        assert mock_send.call_args.kwargs["invoice_type"] == "purchase"

    def test_target_sale_is_passed_through(self, client, seeded) -> None:
        account, attachment = seeded
        app.dependency_overrides[get_account_for_company] = lambda: account

        with patch(
            "app.api.routes.sync.send_attachment_to_scanner",
            AsyncMock(return_value={"job_id": "j1", "status": "done"}),
        ) as mock_send:
            response = client.post(
                f"/api/v1/email/files/{attachment.id}/send-to-scanner", data={"target": "sale"},
            )

        assert response.status_code == 200
        assert mock_send.call_args.kwargs["invoice_type"] == "sale"

    def test_an_unknown_target_is_a_clean_400(self, client, seeded) -> None:
        account, attachment = seeded
        app.dependency_overrides[get_account_for_company] = lambda: account

        response = client.post(
            f"/api/v1/email/files/{attachment.id}/send-to-scanner", data={"target": "refund"},
        )

        assert response.status_code == 400
