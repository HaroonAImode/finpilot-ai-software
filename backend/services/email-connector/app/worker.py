"""Celery application and durable background sync task."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from celery import Celery
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import assert_usable_encryption_key, get_settings
from app.models import EmailAccount, EmailAccountStatus, EmailSyncJob, EmailSyncStatus
from app.services.rate_limiter import get_rate_limiter
from app.services.sync_orchestrator import SyncOrchestrator

settings = get_settings()

assert_usable_encryption_key(settings.token_encryption_key)
celery_app = Celery(
    "email_connector",
    broker=settings.resolved_celery_broker_url,
    backend=settings.resolved_celery_result_backend,
)
celery_app.conf.update(
    task_track_started=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
)

# Scheduled incremental sync (plan §6, Phase 4). Cheap because it is
# incremental: history.list returns nothing when the mailbox has not changed,
# so a quiet interval costs one API call, not a mailbox walk. Set
# SCHEDULED_SYNC_MINUTES=0 to turn scheduling off entirely.
if settings.scheduled_sync_minutes > 0:
    celery_app.conf.beat_schedule = {
        "sync-all-accounts": {
            "task": "app.worker.sync_all_accounts",
            "schedule": settings.scheduled_sync_minutes * 60.0,
        },
        # A worker process killed mid-sync (OOM, deploy, host restart) leaves
        # its job at "running" forever — nothing in that job's own code path
        # ever runs again to say otherwise. Left unfixed, the account's
        # in-flight check in _queue_scheduled_syncs would treat it as
        # perpetually busy and stop scheduling that account entirely. Runs
        # far less often than the sync itself; there is no urgency, only the
        # eventual need to unstick it.
        "reconcile-stuck-sync-jobs": {
            "task": "app.worker.reconcile_stuck_jobs",
            "schedule": 3600.0,
        },
    }

logger = logging.getLogger(__name__)


async def _run_sync(sync_job_id: UUID) -> None:
    # A Celery worker may execute several tasks in a process. Create and
    # dispose the asyncpg engine per task so no connection survives an
    # asyncio.run() loop and becomes attached to the next task's event loop.
    engine = create_async_engine(settings.database_url, pool_pre_ping=True, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with session_factory() as db:
            sync_job = await db.scalar(select(EmailSyncJob).where(EmailSyncJob.id == sync_job_id))
            if not sync_job:
                logger.error("Sync job %s was not found", sync_job_id)
                return

            account = await db.scalar(select(EmailAccount).where(EmailAccount.id == sync_job.account_id))
            if not account:
                sync_job.status = EmailSyncStatus.failed
                sync_job.errors.append("Email account no longer exists.")
                await db.commit()
                return

            sync_job.status = EmailSyncStatus.running
            await db.commit()

            orchestrator = SyncOrchestrator(
                db=db, account_id=account.id, settings=settings, rate_limiter=get_rate_limiter(),
            )
            await orchestrator.sync(sync_job)
    finally:
        await engine.dispose()


@celery_app.task(name="app.worker.run_sync", bind=True, autoretry_for=(ConnectionError,), retry_backoff=True, max_retries=3)
def run_sync(self, sync_job_id: str) -> None:
    """Run one persisted sync job in the dedicated worker process."""
    asyncio.run(_run_sync(UUID(sync_job_id)))


async def _queue_scheduled_syncs() -> int:
    """Create a sync job per healthy account and hand each to the worker."""
    engine = create_async_engine(settings.database_url, pool_pre_ping=True, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    queued = 0
    try:
        async with session_factory() as db:
            # Only active accounts. A needs_reauth account would fail on every
            # tick and fill the job table with identical failures — the user
            # has to reconnect before a sync can do anything, and the UI
            # already tells them so.
            accounts = list(
                await db.scalars(
                    select(EmailAccount).where(EmailAccount.status == EmailAccountStatus.active)
                )
            )

            for account in accounts:
                # Skip an account that already has work queued or running, so a
                # slow sync does not accumulate a backlog of duplicate jobs
                # behind it every time the schedule fires.
                in_flight = await db.scalar(
                    select(EmailSyncJob).where(
                        EmailSyncJob.account_id == account.id,
                        EmailSyncJob.status.in_([EmailSyncStatus.queued, EmailSyncStatus.running]),
                    )
                )
                if in_flight is not None:
                    logger.info("Skipping scheduled sync for account %s — one is already in flight", account.id)
                    continue

                sync_job = EmailSyncJob(account_id=account.id, status=EmailSyncStatus.queued)
                db.add(sync_job)
                await db.commit()
                await db.refresh(sync_job)
                run_sync.delay(str(sync_job.id))
                queued += 1
    finally:
        await engine.dispose()
    return queued


@celery_app.task(name="app.worker.sync_all_accounts")
def sync_all_accounts() -> int:
    """Celery Beat entry point: queue an incremental sync for every account.

    Returns the number queued, which is what shows up in the task result and
    makes a quiet tick ("0") distinguishable from a broken one in the logs.
    """
    queued = asyncio.run(_queue_scheduled_syncs())
    logger.info("Scheduled sync queued %s account(s)", queued)
    return queued


# A running sync legitimately takes minutes on a large backfill (the first
# real one took ~4 minutes for 85 messages) — this margin has to clear any
# realistic run, or reconciliation would fail jobs that are simply still
# working. Generous on purpose: false "stuck" positives are worse than a
# slightly late cleanup.
STUCK_JOB_THRESHOLD = timedelta(hours=1)


async def _reconcile_stuck_jobs() -> int:
    engine = create_async_engine(settings.database_url, pool_pre_ping=True, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    marked = 0
    try:
        async with session_factory() as db:
            cutoff = datetime.now(timezone.utc) - STUCK_JOB_THRESHOLD
            stuck_jobs = list(
                await db.scalars(
                    select(EmailSyncJob).where(
                        EmailSyncJob.status.in_([EmailSyncStatus.queued, EmailSyncStatus.running]),
                        EmailSyncJob.started_at < cutoff,
                    )
                )
            )
            for job in stuck_jobs:
                job.status = EmailSyncStatus.failed
                job.completed_at = datetime.now(timezone.utc)
                job.errors.append(
                    f"No progress for over {int(STUCK_JOB_THRESHOLD.total_seconds() // 3600)}h — "
                    "the worker likely restarted mid-run. Marked failed so scheduled syncs for "
                    "this account can resume."
                )
                marked += 1
            if marked:
                await db.commit()
    finally:
        await engine.dispose()
    return marked


@celery_app.task(name="app.worker.reconcile_stuck_jobs")
def reconcile_stuck_jobs() -> int:
    marked = asyncio.run(_reconcile_stuck_jobs())
    if marked:
        logger.warning("Reconciled %s sync job(s) stuck past %s", marked, STUCK_JOB_THRESHOLD)
    return marked
