"""GET/PATCH /files/ — read side of synced attachments, and the
send-a-correction path. Tenant isolation is the security property under
test throughout: every query is scoped by account.id, resolved from the
(mocked) authenticated company.
"""
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.tenancy import get_account_for_company
from app.db.session import get_db
from app.main import app
from app.models import EmailAccount, EmailAttachment, EmailMessage, EmailProviderName


def _account(company_id=None) -> EmailAccount:
    return EmailAccount(
        company_id=company_id or uuid.uuid4(), provider=EmailProviderName.gmail,
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
            mine = _account()
            theirs = _account()
            db.add_all([mine, theirs])
            await db.flush()

            my_message = EmailMessage(
                account_id=mine.id, provider_message_id="MSG1", subject="Invoice",
                from_name="Vendor", from_address="billing@vendor.com", has_attachments=True,
            )
            their_message = EmailMessage(
                account_id=theirs.id, provider_message_id="MSG9", subject="Their invoice",
                has_attachments=True,
            )
            db.add_all([my_message, their_message])
            await db.flush()

            my_attachment = EmailAttachment(
                account_id=mine.id, message_id=my_message.id, provider_attachment_id="ATT1",
                filename="invoice.pdf", mimetype="application/pdf", size=5000, category="Invoices",
            )
            their_attachment = EmailAttachment(
                account_id=theirs.id, message_id=their_message.id, provider_attachment_id="ATT9",
                filename="their-invoice.pdf", size=5000,
            )
            db.add_all([my_attachment, their_attachment])
            await db.commit()

            return mine, theirs, my_attachment, their_attachment

    return asyncio.get_event_loop().run_until_complete(_seed())


class TestListFiles:
    def test_returns_only_this_accounts_attachments(self, client, seeded) -> None:
        mine, theirs, my_attachment, their_attachment = seeded
        app.dependency_overrides[get_account_for_company] = lambda: mine

        response = client.get("/api/v1/email/files/")

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["attachments"][0]["id"] == str(my_attachment.id)

    def test_denormalises_sender_and_subject_from_the_parent_message(self, client, seeded) -> None:
        mine, theirs, my_attachment, their_attachment = seeded
        app.dependency_overrides[get_account_for_company] = lambda: mine

        response = client.get("/api/v1/email/files/")

        attachment = response.json()["attachments"][0]
        assert attachment["from_name"] == "Vendor"
        assert attachment["from_address"] == "billing@vendor.com"
        assert attachment["subject"] == "Invoice"

    def test_category_filter(self, client, seeded, db_session_factory) -> None:
        mine, theirs, my_attachment, their_attachment = seeded
        app.dependency_overrides[get_account_for_company] = lambda: mine

        matching = client.get("/api/v1/email/files/?category=Invoices")
        assert matching.json()["total"] == 1

        no_match = client.get("/api/v1/email/files/?category=Contracts")
        assert no_match.json()["total"] == 0


class TestContentStreaming:
    def test_a_not_yet_downloaded_attachment_returns_409(self, client, seeded) -> None:
        mine, theirs, my_attachment, their_attachment = seeded
        app.dependency_overrides[get_account_for_company] = lambda: mine

        response = client.get(f"/api/v1/email/files/{my_attachment.id}/content")

        assert response.status_code == 409

    def test_another_accounts_attachment_id_404s_not_a_cross_tenant_read(self, client, seeded) -> None:
        mine, theirs, my_attachment, their_attachment = seeded
        app.dependency_overrides[get_account_for_company] = lambda: mine

        response = client.get(f"/api/v1/email/files/{their_attachment.id}/content")

        assert response.status_code == 404


class TestCategoryUpdate:
    def test_updating_the_category_marks_it_a_manual_override(self, client, seeded) -> None:
        mine, theirs, my_attachment, their_attachment = seeded
        app.dependency_overrides[get_account_for_company] = lambda: mine

        response = client.patch(
            f"/api/v1/email/files/{my_attachment.id}/category", json={"category": "Contracts"}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["category"] == "Contracts"
        assert body["category_source"] == "manual_override"
        assert body["category_confidence"] == 1.0

    def test_cannot_correct_another_accounts_attachment(self, client, seeded) -> None:
        mine, theirs, my_attachment, their_attachment = seeded
        app.dependency_overrides[get_account_for_company] = lambda: mine

        response = client.patch(
            f"/api/v1/email/files/{their_attachment.id}/category", json={"category": "Contracts"}
        )

        assert response.status_code == 404
