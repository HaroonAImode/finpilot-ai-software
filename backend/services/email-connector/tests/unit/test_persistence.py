"""Upsert behaviour for EmailMessage/EmailAttachment.

Directly guards the bug class the plan doc calls out from Slack's history:
"Upsert, never blind-insert. Look up by (account_id, provider_attachment_id)
and update. A second sync must not duplicate rows. And never overwrite a
category a human set to manual_override."
"""
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.base import Base
from app.services.persistence import EmailPersistenceService, parse_from_header


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


class TestParseFromHeader:
    def test_splits_name_and_address(self) -> None:
        assert parse_from_header("Ayesha Khan <ayesha@vendor.com>") == ("Ayesha Khan", "ayesha@vendor.com")

    def test_bare_address_with_no_display_name(self) -> None:
        assert parse_from_header("billing@vendor.com") == (None, "billing@vendor.com")

    def test_missing_header_does_not_raise(self) -> None:
        assert parse_from_header(None) == (None, None)
        assert parse_from_header("") == (None, None)


class TestMessageUpsert:
    @pytest.mark.asyncio
    async def test_a_message_synced_twice_creates_only_one_row(self, db: AsyncSession) -> None:
        account_id = uuid.uuid4()
        service = EmailPersistenceService(db, account_id)
        headers = {"From": "Vendor <billing@vendor.com>", "Subject": "Invoice"}

        first = await service.get_or_create_message(
            "MSG1", headers=headers, snippet="...", received_at=None, thread_id="T1"
        )
        second = await service.get_or_create_message(
            "MSG1", headers=headers, snippet="...", received_at=None, thread_id="T1"
        )

        assert first.id == second.id

    @pytest.mark.asyncio
    async def test_the_same_provider_id_in_a_different_account_is_a_separate_row(self, db: AsyncSession) -> None:
        """Tenant isolation at the persistence layer: two companies could
        each receive a message that happens to share a raw id shape (it
        won't in practice, but the row must be keyed by account, not just
        provider_message_id, or a lookup could cross tenants)."""
        service_a = EmailPersistenceService(db, uuid.uuid4())
        service_b = EmailPersistenceService(db, uuid.uuid4())
        headers = {"From": "x@y.com"}

        msg_a = await service_a.get_or_create_message("MSG1", headers=headers, snippet=None, received_at=None, thread_id=None)
        msg_b = await service_b.get_or_create_message("MSG1", headers=headers, snippet=None, received_at=None, thread_id=None)

        assert msg_a.id != msg_b.id


class TestAttachmentUpsert:
    @pytest.mark.asyncio
    async def test_an_attachment_synced_twice_updates_the_same_row(self, db: AsyncSession) -> None:
        account_id = uuid.uuid4()
        message_service = EmailPersistenceService(db, account_id)
        message = await message_service.get_or_create_message(
            "MSG1", headers={}, snippet=None, received_at=None, thread_id=None
        )
        record = {"attachment_index": 0, "provider_attachment_id": "ATT1", "filename": "invoice.pdf", "mimetype": "application/pdf", "size": 5000}

        first = await message_service.persist_attachment(message.id, record, ("Invoices", 0.9, "rule_based"))
        second = await message_service.persist_attachment(message.id, record, ("Invoices", 0.9, "rule_based"))

        assert first.id == second.id

    @pytest.mark.asyncio
    async def test_a_resync_dedupes_even_though_gmail_reissues_the_attachment_id(self, db: AsyncSession) -> None:
        """The bug that made the second live sync duplicate all 57 rows:
        Gmail mints a brand-new attachmentId on every messages.get for the
        very same unchanged attachment, so keying on it can never dedupe.
        Position within the message is the stable key."""
        service = EmailPersistenceService(db, uuid.uuid4())
        message = await service.get_or_create_message("MSG1", headers={}, snippet=None, received_at=None, thread_id=None)

        first = await service.persist_attachment(
            message.id,
            {"attachment_index": 0, "provider_attachment_id": "EPHEMERAL-ID-RUN-1", "filename": "invoice.pdf", "mimetype": "application/pdf", "size": 5000},
            ("Invoices", 0.9, "rule_based"),
        )
        second = await service.persist_attachment(
            message.id,
            {"attachment_index": 0, "provider_attachment_id": "TOTALLY-DIFFERENT-ID-RUN-2", "filename": "invoice.pdf", "mimetype": "application/pdf", "size": 5000},
            ("Invoices", 0.9, "rule_based"),
        )

        assert first.id == second.id, "a re-sync must not create a second row"
        # The stored handle is refreshed, since the old one no longer works.
        assert second.provider_attachment_id == "TOTALLY-DIFFERENT-ID-RUN-2"

    @pytest.mark.asyncio
    async def test_two_attachments_in_one_message_stay_separate_rows(self, db: AsyncSession) -> None:
        """Position-based keying must still tell apart two genuinely
        different attachments in the same message — including the awkward
        case where they share a filename (two inline image.png parts)."""
        service = EmailPersistenceService(db, uuid.uuid4())
        message = await service.get_or_create_message("MSG1", headers={}, snippet=None, received_at=None, thread_id=None)

        first = await service.persist_attachment(
            message.id,
            {"attachment_index": 0, "provider_attachment_id": "A", "filename": "image.png", "mimetype": "image/png", "size": 30000},
            ("Images & Screenshots", 0.9, "rule_based"),
        )
        second = await service.persist_attachment(
            message.id,
            {"attachment_index": 1, "provider_attachment_id": "B", "filename": "image.png", "mimetype": "image/png", "size": 40000},
            ("Images & Screenshots", 0.9, "rule_based"),
        )

        assert first.id != second.id

    @pytest.mark.asyncio
    async def test_a_manual_category_override_survives_a_resync(self, db: AsyncSession) -> None:
        account_id = uuid.uuid4()
        service = EmailPersistenceService(db, account_id)
        message = await service.get_or_create_message("MSG1", headers={}, snippet=None, received_at=None, thread_id=None)
        record = {"attachment_index": 0, "provider_attachment_id": "ATT1", "filename": "doc.pdf", "mimetype": "application/pdf", "size": 5000}

        attachment = await service.persist_attachment(message.id, record, ("uncategorized", 0.0, "rule_based"))
        attachment.category = "Contracts"
        attachment.category_source = "manual_override"
        attachment.category_confidence = 1.0
        await db.flush()

        resynced = await service.persist_attachment(message.id, record, ("Invoices", 0.95, "rule_based"))

        assert resynced.category == "Contracts"
        assert resynced.category_source == "manual_override"

    @pytest.mark.asyncio
    async def test_a_resync_still_updates_size_and_filename_when_not_manually_overridden(self, db: AsyncSession) -> None:
        """Google can legitimately re-serve slightly different metadata for
        the same attachment across calls — a plain classifier-assigned
        category should stay in sync, unlike a human's correction."""
        account_id = uuid.uuid4()
        service = EmailPersistenceService(db, account_id)
        message = await service.get_or_create_message("MSG1", headers={}, snippet=None, received_at=None, thread_id=None)

        await service.persist_attachment(
            message.id,
            {"attachment_index": 0, "provider_attachment_id": "ATT1", "filename": "old-name.pdf", "mimetype": "application/pdf", "size": 1000},
            ("uncategorized", 0.0, "rule_based"),
        )
        updated = await service.persist_attachment(
            message.id,
            {"attachment_index": 0, "provider_attachment_id": "ATT1", "filename": "new-name.pdf", "mimetype": "application/pdf", "size": 2000},
            ("Invoices", 0.95, "rule_based"),
        )

        assert updated.filename == "new-name.pdf"
        assert updated.size == 2000
        assert updated.category == "Invoices"
