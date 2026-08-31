"""SyncOrchestrator's non-network logic: which message-listing strategy gets
picked, and that the download step's counters/idempotency behave exactly
like the bugs the plan doc warns were found running the first real sync.

Phase 5 (plan §11a) made this provider-agnostic — SyncOrchestrator now talks
to a MailProviderClient (app/services/providers/base.py), not GmailClient
directly, so the fakes below implement that normalized contract rather than
Gmail's raw wire shapes. Every test's original intent/rationale is kept
unchanged; only the mechanics of how a fake provider is built moved.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models import EmailAccount, EmailAttachment, EmailProviderName, EmailSyncJob
from app.services.providers.base import NormalizedAttachment, NormalizedMessage, ProviderHistoryExpired
from app.services.sync_orchestrator import DEFAULT_SYNC_WINDOW_DAYS, SyncOrchestrator


def _orchestrator(download_result=("s3/key", "hash"), verified=True, raises=None) -> SyncOrchestrator:
    orch = SyncOrchestrator.__new__(SyncOrchestrator)  # skip __init__ and its live deps
    manager = MagicMock()
    if raises is not None:
        manager.store_bytes = AsyncMock(side_effect=raises)
    else:
        manager.store_bytes = AsyncMock(return_value=download_result)
    manager.verify_s3_file = AsyncMock(return_value=verified)
    orch.download_manager = manager
    return orch


def _account(sync_window_days=None) -> EmailAccount:
    return EmailAccount(
        provider=EmailProviderName.gmail, email_address="a@b.com",
        access_token_encrypted="x", refresh_token_encrypted="y",
        scopes=[], sync_window_days=sync_window_days,
    )


def _attachment(index=0, filename="invoice.pdf", provider_ref="att1", size=1000) -> NormalizedAttachment:
    return NormalizedAttachment(index=index, filename=filename, mimetype="application/pdf", size=size, provider_ref=provider_ref)


class _FakeProviderClient:
    """A minimal stand-in for MailProviderClient, implementing only the
    methods _resolve_message_ids actually calls."""

    def __init__(self, *, incremental_ids=None, incremental_raises=None, backfill_ids=None):
        self._incremental_ids = incremental_ids or []
        self._incremental_raises = incremental_raises
        self._backfill_ids = backfill_ids or []
        self.backfill_window: int | None = None
        self.next_cursor: str | None = None

    async def list_message_ids_since(self, cursor: str):
        if self._incremental_raises:
            raise self._incremental_raises
        for mid in self._incremental_ids:
            yield mid

    async def list_message_ids_backfill(self, window_days: int):
        self.backfill_window = window_days
        for mid in self._backfill_ids:
            yield mid


class TestResolveMessageIds:
    @pytest.mark.asyncio
    async def test_no_cursor_uses_a_bounded_backfill(self) -> None:
        orch = SyncOrchestrator.__new__(SyncOrchestrator)
        client = _FakeProviderClient(backfill_ids=["M1", "M2"])
        account = _account()  # no sync_cursor

        ids = await orch._resolve_message_ids(client, account)

        assert ids == ["M1", "M2"]
        assert client.backfill_window == DEFAULT_SYNC_WINDOW_DAYS

    @pytest.mark.asyncio
    async def test_an_explicit_window_overrides_the_default(self) -> None:
        orch = SyncOrchestrator.__new__(SyncOrchestrator)
        client = _FakeProviderClient(backfill_ids=[])
        account = _account(sync_window_days=30)

        await orch._resolve_message_ids(client, account)

        assert client.backfill_window == 30

    @pytest.mark.asyncio
    async def test_an_existing_cursor_uses_incremental_listing(self) -> None:
        orch = SyncOrchestrator.__new__(SyncOrchestrator)
        client = _FakeProviderClient(incremental_ids=["M3"], backfill_ids=["SHOULD_NOT_BE_USED"])
        account = _account()
        account.sync_cursor = "12345"

        ids = await orch._resolve_message_ids(client, account)

        assert ids == ["M3"]

    @pytest.mark.asyncio
    async def test_a_stale_cursor_falls_back_to_backfill_instead_of_reporting_nothing_new(self) -> None:
        """Both providers only retain incremental-sync history for a bounded
        window; a cursor older than that must recover, not silently look
        like 'nothing changed'."""
        orch = SyncOrchestrator.__new__(SyncOrchestrator)
        client = _FakeProviderClient(incremental_raises=ProviderHistoryExpired("too old"), backfill_ids=["M4"])
        account = _account()
        account.sync_cursor = "very-old-cursor"

        ids = await orch._resolve_message_ids(client, account)

        assert ids == ["M4"]

    @pytest.mark.asyncio
    async def test_a_non_expiry_error_from_incremental_listing_is_not_swallowed(self) -> None:
        orch = SyncOrchestrator.__new__(SyncOrchestrator)
        client = _FakeProviderClient(incremental_raises=RuntimeError("connection reset"))
        account = _account()
        account.sync_cursor = "12345"

        with pytest.raises(RuntimeError):
            await orch._resolve_message_ids(client, account)


class TestDownloadCounters:
    @pytest.mark.asyncio
    async def test_successful_download_increments_attachments_downloaded(self) -> None:
        job = EmailSyncJob(attachments_downloaded=0, attachments_failed=0)
        attachment = EmailAttachment(filename="invoice.pdf", downloaded=False, size=1000)
        orch = _orchestrator()
        client = MagicMock()
        client.get_attachment_bytes = AsyncMock(return_value=b"pdf-bytes")

        await orch._download_attachment(client, attachment, "m1", _attachment(), job)

        assert attachment.downloaded is True
        assert job.attachments_downloaded == 1
        assert job.attachments_failed == 0

    @pytest.mark.asyncio
    async def test_the_s3_key_uses_our_own_row_id_not_the_providers_attachment_id(self) -> None:
        """Found live on the first real Gmail sync: every attachment failed
        to store with XMinioInvalidObjectName. Gmail's attachmentId is
        routinely 300-400+ characters — far past the ~255-byte limit MinIO
        enforces on a single "/"-separated S3 key segment — so it can never
        be used as the key prefix, only our own short internal id can. Kept
        this way for Outlook too even though its ids are shorter: nothing
        about the storage layer should have to trust a provider's id shape."""
        import uuid

        job = EmailSyncJob(attachments_downloaded=0, attachments_failed=0)
        attachment = EmailAttachment(id=uuid.uuid4(), filename="invoice.pdf", downloaded=False, size=1000)
        orch = _orchestrator()
        client = MagicMock()
        client.get_attachment_bytes = AsyncMock(return_value=b"pdf-bytes")
        huge_provider_id = "A" * 400  # realistic length, per the live Gmail sync's logs

        await orch._download_attachment(client, attachment, "m1", _attachment(provider_ref=huge_provider_id), job)

        call_args = orch.download_manager.store_bytes.call_args
        assert call_args.args[0] == str(attachment.id)
        assert call_args.args[0] != huge_provider_id

    @pytest.mark.asyncio
    async def test_counters_accumulate_across_attachments(self) -> None:
        job = EmailSyncJob(attachments_downloaded=0, attachments_failed=0)
        orch = _orchestrator()
        client = MagicMock()
        client.get_attachment_bytes = AsyncMock(return_value=b"pdf-bytes")

        for i in range(3):
            attachment = EmailAttachment(filename=f"f{i}.pdf", downloaded=False, size=1000)
            await orch._download_attachment(client, attachment, "m1", _attachment(index=i, filename=f"f{i}.pdf", provider_ref=f"att{i}"), job)

        assert job.attachments_downloaded == 3

    @pytest.mark.asyncio
    async def test_a_fetch_failure_counts_as_failed_not_downloaded(self) -> None:
        job = EmailSyncJob(attachments_downloaded=0, attachments_failed=0)
        attachment = EmailAttachment(filename="invoice.pdf", downloaded=False, size=1000)
        orch = _orchestrator()
        client = MagicMock()
        client.get_attachment_bytes = AsyncMock(side_effect=RuntimeError("connection reset"))

        await orch._download_attachment(client, attachment, "m1", _attachment(), job)

        assert attachment.download_failed is True
        assert job.attachments_failed == 1
        assert job.attachments_downloaded == 0

    @pytest.mark.asyncio
    async def test_failed_s3_verification_counts_as_failed(self) -> None:
        job = EmailSyncJob(attachments_downloaded=0, attachments_failed=0)
        attachment = EmailAttachment(filename="invoice.pdf", downloaded=False, size=1000)
        orch = _orchestrator(verified=False)
        client = MagicMock()
        client.get_attachment_bytes = AsyncMock(return_value=b"pdf-bytes")

        await orch._download_attachment(client, attachment, "m1", _attachment(), job)

        assert attachment.download_failed is True
        assert attachment.download_error == "S3 verification failed"
        assert job.attachments_failed == 1

    @pytest.mark.asyncio
    async def test_a_retry_after_failure_clears_the_failed_flags(self) -> None:
        """An attachment that failed once and is retried on a later sync must
        not stay stuck showing a stale error after it succeeds."""
        job = EmailSyncJob(attachments_downloaded=0, attachments_failed=0)
        attachment = EmailAttachment(
            filename="invoice.pdf", downloaded=False, download_failed=True,
            download_error="previous failure", size=1000,
        )
        orch = _orchestrator()
        client = MagicMock()
        client.get_attachment_bytes = AsyncMock(return_value=b"pdf-bytes")

        await orch._download_attachment(client, attachment, "m1", _attachment(), job)

        assert attachment.download_failed is False
        assert attachment.download_error is None


class _FakeClientOneMessage:
    """A minimal message with one attachment that passes should_import, for
    exercising _sync_message end to end against a real DB session."""

    def __init__(self, headers=None):
        self._headers = headers or {"Subject": "Invoice"}

    async def get_message(self, message_id: str) -> NormalizedMessage:
        return NormalizedMessage(
            provider_message_id=message_id,
            thread_id="T1",
            subject=self._headers.get("Subject"),
            snippet="...",
            received_at=None,
            raw_headers=self._headers,
            attachments=[NormalizedAttachment(index=0, filename="invoice.pdf", mimetype="application/pdf", size=50_000, provider_ref="ATT1")],
        )


class TestSessionRecoversAfterAPersistenceFailure:
    """Found live on the very first real sync: a flush error deep inside
    _sync_message (there, an over-narrow VARCHAR column; here, reproduced
    with a NOT NULL violation) leaves the whole SQLAlchemy session unusable
    for any further statement — including the caller's own commit — even
    though the exception was caught right where it happened. Without an
    explicit rollback, the sync job never gets a recorded failure; it's
    stuck showing "running" forever."""

    @pytest.mark.asyncio
    async def test_a_flush_error_is_recorded_and_the_session_stays_usable(self, monkeypatch) -> None:
        import uuid

        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.models.base import Base
        from app.services.categorization import get_categorization_service
        from app.services.persistence import EmailPersistenceService

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        async with session_factory() as db:
            sync_job = EmailSyncJob(account_id=uuid.uuid4(), errors=[])
            db.add(sync_job)
            await db.commit()

            orch = SyncOrchestrator.__new__(SyncOrchestrator)
            orch.db = db
            orch.categorization_service = get_categorization_service()

            persistence = EmailPersistenceService(db, uuid.uuid4())

            async def _broken_persist_attachment(message_id, record, categorization_result):
                # filename is NOT NULL — this reproduces a genuine flush-time
                # IntegrityError, the same shape of failure the too-narrow
                # VARCHAR column produced against real Postgres.
                bad = EmailAttachment(
                    account_id=uuid.uuid4(), message_id=message_id,
                    provider_attachment_id="x", filename=None, size=100,
                )
                db.add(bad)
                await db.flush()
                return bad

            monkeypatch.setattr(persistence, "persist_attachment", _broken_persist_attachment)

            await orch._sync_message(_FakeClientOneMessage(), persistence, sync_job, "MSG1", [])

            assert len(sync_job.errors) == 1
            assert "MSG1" in sync_job.errors[0]

            # The real assertion: the session must still accept new work.
            # Before the fix, this next statement raised PendingRollbackError.
            db.add(EmailSyncJob(account_id=uuid.uuid4(), errors=[]))
            await db.commit()

        await engine.dispose()


class TestSyncCommitsOnEveryExitPath:
    """sync() used to rely on a single `finally: await self.db.commit()` to
    cover every exit. Splitting failure recovery into its own commit (so it
    can roll back first) accidentally dropped that safety net for the two
    early `return`s — found by re-reading the diff, not live, but the
    failure mode is the same one live testing kept surfacing all afternoon:
    a job silently never gets its final state persisted."""

    @pytest.mark.asyncio
    async def test_a_missing_account_is_recorded_as_failed_and_committed(self) -> None:
        import uuid

        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.models.base import Base

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        async with session_factory() as db:
            missing_account_id = uuid.uuid4()
            sync_job = EmailSyncJob(account_id=missing_account_id, errors=[])
            db.add(sync_job)
            await db.commit()
            job_id = sync_job.id

            orch = SyncOrchestrator.__new__(SyncOrchestrator)
            orch.db = db
            orch.account_id = missing_account_id

            await orch.sync(sync_job)

        # A fresh session, not the one sync() used — proves the failure state
        # actually reached the database rather than living only in memory.
        async with session_factory() as fresh_db:
            from app.models import EmailSyncStatus

            reloaded = await fresh_db.scalar(select(EmailSyncJob).where(EmailSyncJob.id == job_id))
            assert reloaded.status == EmailSyncStatus.failed
            assert "no longer exists" in reloaded.errors[0]

        await engine.dispose()


class TestErrorListIsActuallyMutable:
    """The exact bug the plan doc calls out: a plain ARRAY column silently
    drops in-place `errors.append(...)` — SQLAlchemy never sees the mutation,
    so it's missing after a real commit+reload even though the Python list
    looks correct in memory. EmailSyncJob.errors must be a MutableList."""

    @pytest.mark.asyncio
    async def test_an_appended_error_survives_a_commit_and_reload(self) -> None:
        import uuid

        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.models.base import Base

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=True)

        account_id = uuid.uuid4()
        async with session_factory() as db:
            job = EmailSyncJob(account_id=account_id, errors=[])
            db.add(job)
            await db.flush()
            job_id = job.id
            job.errors.append("Message M1: boom")  # in-place mutation, not reassignment
            await db.commit()

        async with session_factory() as db:
            reloaded = await db.scalar(select(EmailSyncJob).where(EmailSyncJob.id == job_id))
            assert reloaded.errors == ["Message M1: boom"]

        await engine.dispose()
