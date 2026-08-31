"""Celery application and durable background sync task."""

import asyncio
import logging
from uuid import UUID

from celery import Celery
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import assert_usable_encryption_key, get_settings
from app.models import Installation, SyncJob, SyncStatus
from app.services.rate_limiter import get_rate_limiter
from app.services.sync_orchestrator import SyncOrchestrator

settings = get_settings()

# The worker decrypts bot tokens to call Slack, so a bad key breaks every sync
# job. Fail at boot instead of marking each job failed one by one.
assert_usable_encryption_key(settings.token_encryption_key)
celery_app = Celery(
    "slack_connector",
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

logger = logging.getLogger(__name__)


async def _run_sync(
    sync_job_id: UUID, full_resync: bool = False, conversation_ids: list[str] | None = None
) -> None:
    # A Celery worker may execute several tasks in a process.  Create and
    # dispose the asyncpg engine per task so no connection survives an
    # asyncio.run() loop and becomes attached to the next task's event loop.
    engine = create_async_engine(settings.database_url, pool_pre_ping=True, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
      async with session_factory() as db:
        sync_job = await db.scalar(select(SyncJob).where(SyncJob.id == sync_job_id))
        if not sync_job:
            logger.error("Sync job %s was not found", sync_job_id)
            return

        installation = await db.scalar(select(Installation).where(Installation.id == sync_job.installation_id))
        if not installation:
            sync_job.status = SyncStatus.failed
            sync_job.errors.append("Installation no longer exists.")
            await db.commit()
            return

        sync_job.status = SyncStatus.running
        await db.commit()
        # The window is a property of the installation, so a scheduled sync uses
        # the same scope the user chose in the UI. full_resync is per-run.
        orchestrator = SyncOrchestrator(
            db=db,
            installation_id=installation.id,
            bot_token_encrypted=installation.bot_token_encrypted,
            settings=settings,
            rate_limiter=get_rate_limiter(),
            sync_window_days=installation.sync_window_days,
            full_resync=full_resync,
            conversation_ids=conversation_ids,
        )
        await orchestrator.sync(sync_job)
    finally:
      await engine.dispose()


@celery_app.task(name="app.worker.run_sync", bind=True, autoretry_for=(ConnectionError,), retry_backoff=True, max_retries=3)
def run_sync(
    self, sync_job_id: str, full_resync: bool = False, conversation_ids: list[str] | None = None
) -> None:
    """Run one persisted sync job in the dedicated worker process."""
    asyncio.run(_run_sync(UUID(sync_job_id), full_resync=full_resync, conversation_ids=conversation_ids))
