"""DELETE /files/{id} — soft delete. An EmailAttachment is a copy of a real
message, so deleting it here must only hide it from FinPilot: never touch
the mailbox, never touch the S3 copy, and never come back on the next sync
(persist_attachment's upsert never touches deleted_at). Distinct from
POST /reject, which is a pre-download decision on something still sitting
in needs_review.
"""
import uuid
from datetime import datetime, timezone

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


class TestDelete:
    def test_a_deleted_attachment_disappears_from_the_list(self, client, seeded) -> None:
        mine, _, my_attachment, _ = seeded
        app.dependency_overrides[get_account_for_company] = lambda: mine

        response = client.delete(f"/api/v1/email/files/{my_attachment.id}")
        assert response.status_code == 204

        assert client.get("/api/v1/email/files/").json()["total"] == 0

    def test_a_deleted_attachment_404s_on_every_other_route_too(self, client, seeded) -> None:
        """Not just hidden from the list — every route that resolves through
        _get_attachment_scoped has to agree it's gone."""
        mine, _, my_attachment, _ = seeded
        app.dependency_overrides[get_account_for_company] = lambda: mine

        client.delete(f"/api/v1/email/files/{my_attachment.id}")
        assert client.get(f"/api/v1/email/files/{my_attachment.id}/content").status_code == 404
        assert client.patch(
            f"/api/v1/email/files/{my_attachment.id}/category", json={"category": "Receipts"},
        ).status_code == 404

    def test_deleting_another_account_s_attachment_is_a_404(self, client, seeded) -> None:
        """The security property: deleting must never reach an attachment
        outside this account, even by guessing a valid id from another tenant."""
        mine, theirs, _, their_attachment = seeded
        app.dependency_overrides[get_account_for_company] = lambda: mine

        response = client.delete(f"/api/v1/email/files/{their_attachment.id}")
        assert response.status_code == 404

        app.dependency_overrides[get_account_for_company] = lambda: theirs
        assert client.get("/api/v1/email/files/").json()["total"] == 1

    def test_deleting_an_unknown_attachment_is_a_404(self, client, seeded) -> None:
        mine, _, _, _ = seeded
        app.dependency_overrides[get_account_for_company] = lambda: mine
        assert client.delete(f"/api/v1/email/files/{uuid.uuid4()}").status_code == 404

    def test_deleting_twice_is_still_a_404_the_second_time(self, client, seeded) -> None:
        mine, _, my_attachment, _ = seeded
        app.dependency_overrides[get_account_for_company] = lambda: mine

        assert client.delete(f"/api/v1/email/files/{my_attachment.id}").status_code == 204
        assert client.delete(f"/api/v1/email/files/{my_attachment.id}").status_code == 404
