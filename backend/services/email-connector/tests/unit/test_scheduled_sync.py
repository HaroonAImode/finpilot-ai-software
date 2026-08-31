"""Celery Beat's two periodic tasks — plan §6/Phase 4:
- queue an incremental sync per account, skipping ones already in flight
- reconcile a job stuck at "running"/"queued" past a generous threshold,
  which is what stops a crashed worker from silently freezing an account's
  scheduled syncs forever (the in-flight check would otherwise treat a
  permanently-stuck job as perpetually busy).
"""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import (
    EmailAccount, EmailAccountStatus, EmailProviderName, EmailSyncJob, EmailSyncStatus,
)
from app.models.base import Base


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


def _account(status=EmailAccountStatus.active) -> EmailAccount:
    return EmailAccount(
        company_id=uuid.uuid4(), provider=EmailProviderName.gmail, email_address="a@b.com",
        access_token_encrypted="x", refresh_token_encrypted="y",
        token_expires_at=datetime.now(timezone.utc), scopes=[], status=status,
    )


class TestQueueScheduledSyncs:
    @pytest.mark.asyncio
    async def test_queues_one_job_per_active_account(self, db: AsyncSession) -> None:
        from app import worker

        a1, a2 = _account(), _account()
        db.add_all([a1, a2])
        await db.commit()

        with patch.object(worker, "async_sessionmaker", return_value=lambda: db), \
             patch.object(worker, "create_async_engine", return_value=AsyncMock(dispose=AsyncMock())), \
             patch.object(worker.run_sync, "delay") as delay:
            queued = await worker._queue_scheduled_syncs()

        assert queued == 2
        assert delay.call_count == 2

    @pytest.mark.asyncio
    async def test_a_needs_reauth_account_is_skipped(self, db: AsyncSession) -> None:
        """Would fail on every tick until the user reconnects — the UI
        already tells them so; filling the job table with identical
        failures adds nothing."""
        from app import worker

        db.add(_account(status=EmailAccountStatus.needs_reauth))
        await db.commit()

        with patch.object(worker, "async_sessionmaker", return_value=lambda: db), \
             patch.object(worker, "create_async_engine", return_value=AsyncMock(dispose=AsyncMock())), \
             patch.object(worker.run_sync, "delay") as delay:
            queued = await worker._queue_scheduled_syncs()

        assert queued == 0
        delay.assert_not_called()

    @pytest.mark.asyncio
    async def test_an_account_with_an_in_flight_job_is_skipped(self, db: AsyncSession) -> None:
        """The point of the whole check: a slow sync must not accumulate a
        backlog of duplicate jobs behind it every time the schedule fires."""
        from app import worker

        account = _account()
        db.add(account)
        await db.flush()
        db.add(EmailSyncJob(account_id=account.id, status=EmailSyncStatus.running))
        await db.commit()

        with patch.object(worker, "async_sessionmaker", return_value=lambda: db), \
             patch.object(worker, "create_async_engine", return_value=AsyncMock(dispose=AsyncMock())), \
             patch.object(worker.run_sync, "delay") as delay:
            queued = await worker._queue_scheduled_syncs()

        assert queued == 0
        delay.assert_not_called()


class TestReconcileStuckJobs:
    @pytest.mark.asyncio
    async def test_a_job_stuck_past_the_threshold_is_marked_failed(self, db: AsyncSession) -> None:
        from app import worker

        account = _account()
        db.add(account)
        await db.flush()
        stuck = EmailSyncJob(
            account_id=account.id, status=EmailSyncStatus.running,
            started_at=datetime.now(timezone.utc) - worker.STUCK_JOB_THRESHOLD - timedelta(minutes=5),
            errors=[],
        )
        db.add(stuck)
        await db.commit()

        with patch.object(worker, "async_sessionmaker", return_value=lambda: db), \
             patch.object(worker, "create_async_engine", return_value=AsyncMock(dispose=AsyncMock())):
            marked = await worker._reconcile_stuck_jobs()

        assert marked == 1
        assert stuck.status == EmailSyncStatus.failed
        assert stuck.completed_at is not None
        assert len(stuck.errors) == 1

    @pytest.mark.asyncio
    async def test_a_job_still_within_the_threshold_is_left_alone(self, db: AsyncSession) -> None:
        """A real backfill legitimately takes minutes — this must not fail
        work that is simply still running."""
        from app import worker

        account = _account()
        db.add(account)
        await db.flush()
        recent = EmailSyncJob(
            account_id=account.id, status=EmailSyncStatus.running,
            started_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            errors=[],
        )
        db.add(recent)
        await db.commit()

        with patch.object(worker, "async_sessionmaker", return_value=lambda: db), \
             patch.object(worker, "create_async_engine", return_value=AsyncMock(dispose=AsyncMock())):
            marked = await worker._reconcile_stuck_jobs()

        assert marked == 0
        assert recent.status == EmailSyncStatus.running

    @pytest.mark.asyncio
    async def test_a_completed_job_is_never_touched_regardless_of_age(self, db: AsyncSession) -> None:
        from app import worker

        account = _account()
        db.add(account)
        await db.flush()
        old_but_done = EmailSyncJob(
            account_id=account.id, status=EmailSyncStatus.completed,
            started_at=datetime.now(timezone.utc) - timedelta(days=30),
            errors=[],
        )
        db.add(old_but_done)
        await db.commit()

        with patch.object(worker, "async_sessionmaker", return_value=lambda: db), \
             patch.object(worker, "create_async_engine", return_value=AsyncMock(dispose=AsyncMock())):
            marked = await worker._reconcile_stuck_jobs()

        assert marked == 0
        assert old_but_done.status == EmailSyncStatus.completed
