import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models import (
    EmailAccount, EmailAttachment, EmailSyncJob, EmailSyncStatus, ReviewStatus, SenderRule,
)
from app.services.attachment_filter import decide_review_status, should_import
from app.services.categorization import get_categorization_service
from app.services.download_manager import DownloadManager
from app.services.providers.base import MailProviderClient, NormalizedAttachment, ProviderHistoryExpired
from app.services.providers.registry import get_provider_client_class
from app.services.providers.token_manager import TokenRefreshError, get_valid_access_token
from app.services.persistence import EmailPersistenceService
from app.services.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

# Applied when EmailAccount.sync_window_days is None — unlike Slack, where
# None on the equivalent field means "all history" chosen deliberately, no
# one has ever explicitly chosen "all time" for email (Phase 3's picker for
# it doesn't exist yet), so None here is "no choice made" rather than "an
# explicit choice for everything". A decade-old mailbox is a real privacy
# and storage risk to sync unbounded — see docs/email-connector-plan.md §5/§9.
DEFAULT_SYNC_WINDOW_DAYS = 180


class SyncOrchestrator:
    """
    Orchestrates one sync run for one EmailAccount, against whichever
    provider it belongs to (Gmail or Outlook — plan §11a). Provider
    differences are resolved once, at the top, by picking a
    MailProviderClient implementation off account.provider; every step below
    is written against the normalized contract in
    app/services/providers/base.py and does not know or care which provider
    it's actually talking to.

    1. Refresh the access token if it's expiring soon
    2. Find new/changed messages — incremental listing if there's a cursor,
       otherwise a bounded listing for the first sync
    3. For each message, filter its attachments, categorise, persist, download
    4. Advance the account's sync cursor
    """

    def __init__(self, db: AsyncSession, account_id, settings: Settings, rate_limiter: RateLimiter):
        self.db = db
        self.account_id = account_id
        self.settings = settings
        self.rate_limiter = rate_limiter
        self.categorization_service = get_categorization_service()
        self.download_manager = DownloadManager(settings)

    async def sync(self, sync_job: EmailSyncJob) -> None:
        try:
            account = await self.db.scalar(select(EmailAccount).where(EmailAccount.id == self.account_id))
            if account is None:
                sync_job.status = EmailSyncStatus.failed
                sync_job.errors.append("Email account no longer exists.")
                await self.db.commit()
                return

            try:
                access_token = await get_valid_access_token(account, self.settings)
            except TokenRefreshError as exc:
                sync_job.status = EmailSyncStatus.failed
                sync_job.errors.append(str(exc))
                await self.db.commit()
                return
            await self.db.commit()

            persistence = EmailPersistenceService(self.db, self.account_id)
            client_cls = get_provider_client_class(account.provider)

            async with client_cls(access_token, self.rate_limiter) as client:
                message_ids = await self._resolve_message_ids(client, account)

                sync_job.status = EmailSyncStatus.running
                await self.db.commit()

                # Loaded once per run rather than per attachment: a mailbox
                # sync can touch thousands of attachments and the rule set is
                # small and unchanging for the duration of one run.
                sender_rules = list(
                    await self.db.scalars(
                        select(SenderRule).where(SenderRule.account_id == self.account_id)
                    )
                )

                # Incremental listing can report the same message more than
                # once (e.g. Gmail: added, then labelled; Graph delta: more
                # than one change record) — dedupe within this run so it
                # isn't scanned twice.
                seen: set[str] = set()
                for message_id in message_ids:
                    if message_id in seen:
                        continue
                    seen.add(message_id)
                    await self._sync_message(client, persistence, sync_job, message_id, sender_rules)
                    try:
                        await self.db.commit()
                    except Exception as commit_error:
                        # _sync_message already recovers from a poisoned
                        # session for errors *inside* it — this is the same
                        # failure one level up: this specific commit (of an
                        # otherwise-successful message) can itself fail, e.g.
                        # a dropped DB connection mid-transaction (found live
                        # under real load). Without recovering here too, one
                        # bad commit crashes the whole run instead of costing
                        # just this one message.
                        logger.error("Commit failed after message %s: %s", message_id, commit_error)
                        await self.db.rollback()
                        await self.db.refresh(sync_job)
                        sync_job.errors.append(f"Message {message_id}: commit failed: {commit_error}")
                        await self.db.commit()

                new_cursor = client.next_cursor

            account.sync_cursor = new_cursor
            account.last_synced_at = datetime.now(timezone.utc)
            sync_job.status = EmailSyncStatus.completed
            sync_job.completed_at = datetime.now(timezone.utc)
            logger.info(
                "Email sync completed. Scanned: %s, discovered: %s, downloaded: %s, failed: %s",
                sync_job.messages_scanned, sync_job.attachments_discovered,
                sync_job.attachments_downloaded, sync_job.attachments_failed,
            )

        except Exception as e:
            logger.error("Email sync failed: %s", e, exc_info=True)
            try:
                # The session may already be poisoned by whatever raised
                # above (a flush error deep in the loop) — roll back first
                # so the failure state below actually has a chance to commit,
                # same reasoning as the per-message recovery.
                await self.db.rollback()
                await self.db.refresh(sync_job)
                sync_job.status = EmailSyncStatus.failed
                sync_job.completed_at = datetime.now(timezone.utc)
                sync_job.errors.append(str(e))
                await self.db.commit()
            except Exception as recovery_error:
                # A genuinely dead connection (not just a poisoned
                # transaction) can't be recovered here — logging beats
                # letting this become an unhandled crash that takes the
                # whole Celery task down without a trace of what happened.
                # The job is left as its last successfully-committed state
                # rather than falsely marked failed on a guess.
                logger.error(
                    "Could not record sync failure for job %s: %s", sync_job.id, recovery_error, exc_info=True,
                )

        else:
            await self.db.commit()

    async def _resolve_message_ids(self, client: MailProviderClient, account: EmailAccount) -> list[str]:
        window = account.sync_window_days or DEFAULT_SYNC_WINDOW_DAYS

        if account.sync_cursor:
            try:
                return [mid async for mid in client.list_message_ids_since(account.sync_cursor)]
            except ProviderHistoryExpired:
                # Both providers retain incremental-sync history for a
                # bounded window (Gmail: ~30 days; Graph: its own similar
                # retention) — a cursor older than that can't be resolved.
                # Falling back to a bounded backfill is the documented
                # recovery (plan §6); the alternative, silently yielding
                # zero messages, would look like "nothing new" when the
                # truth is "we lost our place".
                logger.warning(
                    "Sync cursor is too old for account %s; falling back to backfill", account.id,
                )

        return [mid async for mid in client.list_message_ids_backfill(window)]

    async def _sync_message(
        self, client: MailProviderClient, persistence: EmailPersistenceService, sync_job: EmailSyncJob,
        message_id: str, sender_rules: list[SenderRule],
    ) -> None:
        try:
            message = await client.get_message(message_id)
            sync_job.messages_scanned += 1

            db_message = await persistence.get_or_create_message(
                message_id,
                headers=message.raw_headers,
                snippet=message.snippet,
                received_at=message.received_at,
                thread_id=message.thread_id,
            )

            # message.attachments already enumerates every attachment at a
            # fixed position (NormalizedAttachment.index), before filtering,
            # so that index identifies a fixed position in the message
            # rather than a position among whatever happened to pass the
            # filter. Otherwise loosening the filter later would renumber
            # existing attachments and silently orphan their rows.
            for attachment in message.attachments:
                ok, _reason = should_import(attachment.filename, attachment.size)
                if not ok:
                    continue

                category, confidence, source = self.categorization_service.categorize(
                    filename=attachment.filename, file_type=attachment.mimetype,
                )

                status, reason = decide_review_status(sender_rules, db_message.from_address, confidence)
                if status is None:
                    # Denied sender: not stored at all. Keeping a row would
                    # mean keeping a record of who emails this mailbox, which
                    # is exactly what a deny rule is asking us not to do.
                    continue

                sync_job.attachments_discovered += 1

                attachment_obj = await persistence.persist_attachment(
                    db_message.id,
                    {
                        "attachment_index": attachment.index,
                        "provider_attachment_id": attachment.provider_ref,
                        "filename": attachment.filename,
                        "mimetype": attachment.mimetype,
                        "size": attachment.size,
                        "review_status": status,
                        "review_reason": reason,
                    },
                    (category, confidence, source),
                )

                # Only imported attachments are fetched. needs_review stays
                # metadata-only until a human approves it (plan §5b) — that
                # is the entire privacy argument for the tray existing, so
                # downloading "just in case" here would defeat it.
                # Idempotent besides: an attachment already downloaded by an
                # earlier run is not re-fetched.
                if attachment_obj.review_status == ReviewStatus.imported and not attachment_obj.downloaded:
                    await self._download_attachment(client, attachment_obj, message_id, attachment, sync_job)

        except Exception as e:
            logger.error("Failed to sync message %s: %s", message_id, e)
            # A flush/commit failure (e.g. a value too long for a column —
            # found live on the first real sync) leaves the whole session
            # unusable for any further query until rolled back, even though
            # the exception was caught here. Without this, the *next*
            # statement anywhere in this session — including the caller's
            # own commit — raises PendingRollbackError, which itself was
            # uncaught and crashed the whole task, leaving this job stuck
            # showing "running" forever with no error ever recorded.
            await self.db.rollback()
            await self.db.refresh(sync_job)
            sync_job.errors.append(f"Message {message_id}: {e}")
            await self.db.commit()

    async def _download_attachment(
        self, client: MailProviderClient, attachment_obj: EmailAttachment, message_id: str,
        attachment: NormalizedAttachment, sync_job: EmailSyncJob,
    ) -> None:
        try:
            content = await client.get_attachment_bytes(message_id, attachment)
            # Keyed by our own row id, not the provider's attachment id —
            # found live on the first real Gmail sync: Gmail's attachmentId
            # is routinely 300-400+ characters, and MinIO (backed by a
            # normal filesystem) rejects any single "/"-separated path
            # segment over ~255 bytes as XMinioInvalidObjectName. Every
            # attachment failed to store until this changed to a short,
            # internally-owned identifier — applies equally to Outlook,
            # whose ids are shorter but there is no reason to trust that.
            s3_key, sha256_hash = await self.download_manager.store_bytes(
                str(attachment_obj.id), attachment.filename, content
            )

            if not await self.download_manager.verify_s3_file(s3_key, sha256_hash):
                attachment_obj.download_failed = True
                attachment_obj.download_error = "S3 verification failed"
                sync_job.attachments_failed += 1
                logger.warning("S3 verification failed for attachment %s", attachment.provider_ref)
                return

            attachment_obj.s3_key = s3_key
            attachment_obj.sha256_hash = sha256_hash
            attachment_obj.downloaded = True
            attachment_obj.download_failed = False
            attachment_obj.download_error = None
            sync_job.attachments_downloaded += 1

        except Exception as e:
            logger.error("Failed to download attachment %s: %s", attachment.provider_ref, e)
            attachment_obj.download_failed = True
            attachment_obj.download_error = str(e)
            sync_job.attachments_failed += 1
