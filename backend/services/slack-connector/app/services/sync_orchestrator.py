import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import TokenCipher
from app.models import Conversation, File, Installation, SyncCursor, SyncJob, SyncStatus, Workspace
from app.services.categorization import get_categorization_service
from app.services.download_manager import DownloadManager
from app.services.rate_limiter import RateLimiter
from app.services.slack.discovery import DiscoveryPersistenceService, DiscoveryService

logger = logging.getLogger(__name__)


def _slack_error_code(exc: Exception) -> str:
    """Pull Slack's machine-readable error code out of an exception message.

    Slack surfaces failures as short codes — not_in_channel, channel_not_found —
    and those are worth storing verbatim rather than the whole sentence: the
    code is stable enough to map to advice, while the surrounding text is not.
    Falls back to a truncated message so an unexpected failure is still visible.
    """
    text = str(exc)
    marker = "Slack API error: "
    if marker in text:
        return text.split(marker, 1)[1].strip()[:128]
    return text[:128]


class SyncOrchestrator:
    """
    Orchestrates the full sync process:
    1. List conversations
    2. Walk each conversation for messages/files
    3. Extract files with source linkage
    4. Categorize files
    5. Download files to S3
    6. Persist to database with resumable cursors
    """

    def __init__(
        self,
        db: AsyncSession,
        installation_id: UUID,
        bot_token_encrypted: str,
        settings,
        rate_limiter: RateLimiter,
        sync_window_days: int | None = None,
        full_resync: bool = False,
        conversation_ids: list[str] | None = None,
    ):
        self.db = db
        self.installation_id = installation_id
        self.bot_token_encrypted = bot_token_encrypted
        self.settings = settings
        self.rate_limiter = rate_limiter
        # How far back to look. None means all history.
        self.sync_window_days = sync_window_days
        # True re-walks everything inside the window, ignoring what was already
        # processed — the remedy when an incremental run has missed something.
        self.full_resync = full_resync
        # Local Conversation UUIDs (as strings) to sync, or None for everything.
        # Resolved to Slack ids inside sync(), scoped to this installation —
        # never trusted as-is, since these strings cross a process boundary
        # (API request -> Celery -> here) with no re-check in between.
        self.conversation_ids = conversation_ids
        
        # Decrypt bot token
        cipher = TokenCipher(settings.token_encryption_key)
        self.bot_token = cipher.decrypt(bot_token_encrypted)
        
        self.categorization_service = get_categorization_service()
        self.download_manager = DownloadManager(settings)

    async def start_sync(self) -> SyncJob:
        """Start a new sync job."""
        sync_job = SyncJob(installation_id=self.installation_id, status=SyncStatus.running)
        self.db.add(sync_job)
        await self.db.flush()
        return sync_job

    async def sync(self, sync_job: SyncJob) -> None:
        """Execute the full sync process."""
        try:
            from sqlalchemy.orm import selectinload
            
            # Get workspace info for logging (eager load workspace to avoid lazy loading in background task)
            installation = await self.db.scalar(
                select(Installation)
                .filter(Installation.id == self.installation_id)
                .options(selectinload(Installation.workspace))
            )
            workspace_name = installation.workspace.slack_team_id if installation and installation.workspace else "unknown"

            logger.info(f"Starting sync for workspace {workspace_name}")

            persistence = DiscoveryPersistenceService(self.db, self.installation_id)

            async with DiscoveryService(
                self.bot_token, self.settings, self.rate_limiter
            ) as discovery:
                # Discover the accessible scope first. This is a small, paginated
                # API request and lets the UI show a truthful total immediately.
                conversations = [conv_data async for conv_data in discovery.list_conversations()]

                if self.conversation_ids:
                    conversations = await self._filter_to_requested_conversations(conversations)

                sync_job.status = SyncStatus.running
                sync_job.total_conversations = len(conversations)
                await self.db.commit()

                for conv_data in conversations:
                    await self._sync_conversation(
                        discovery, persistence, sync_job, conv_data
                    )
                    await self.db.commit()  # Commit after each conversation

            sync_job.status = SyncStatus.completed
            sync_job.completed_at = datetime.utcnow()
            logger.info(f"Sync completed. Discovered: {sync_job.files_discovered}, Downloaded: {sync_job.files_downloaded}, Failed: {sync_job.files_failed}")

        except Exception as e:
            logger.error(f"Sync failed: {e}", exc_info=True)
            sync_job.status = SyncStatus.failed
            sync_job.completed_at = datetime.utcnow()
            sync_job.errors.append(str(e))

        finally:
            await self.db.commit()

    async def _sync_conversation(
        self,
        discovery: DiscoveryService,
        persistence: DiscoveryPersistenceService,
        sync_job: SyncJob,
        conv_data: dict,
    ) -> None:
        """Sync a single conversation."""
        conversation: Conversation | None = None
        try:
            conv_id = conv_data.get("id")
            logger.info(f"Processing conversation: {conv_data.get('name', conv_id)}")

            # Get or create conversation record
            conversation = await persistence.get_or_create_conversation(
                conv_data, self.settings.slack_client_id,  # Using as placeholder for team_id lookup
                discovery=discovery,
            )

            # Get or create sync cursor for this conversation
            sync_cursor = await self._get_or_create_cursor(sync_job.id, conversation.id)

            oldest = self._resolve_oldest(conversation)
            # Track the newest message seen so the next run can start from here.
            # Slack returns history newest-first, but relying on ordering would be
            # fragile, so take the maximum explicitly.
            newest_ts = conversation.last_seen_ts

            message_count = 0
            async for message, thread_ts in discovery.walk_conversation_messages(conv_id, oldest=oldest):
                message_count += 1

                ts = message.get("ts")
                if ts and (newest_ts is None or float(ts) > float(newest_ts)):
                    newest_ts = ts

                # Extract files from message
                file_records = await discovery.extract_files_from_message(
                    message,
                    conversation.id,
                    message.get("ts"),
                    thread_ts,
                )

                for file_record in file_records:
                    sync_job.files_discovered += 1

                    # Categorize the file
                    category, confidence, category_source = self.categorization_service.categorize(
                        filename=file_record["filename"],
                        title=file_record.get("title"),
                        channel_name=conversation.name,
                        file_type=file_record.get("file_type"),
                    )

                    # Persist file record
                    file_obj = await persistence.persist_file(
                        file_record,
                        conversation.id,
                        (category, confidence, category_source),
                    )

                    # Download if not external
                    if not file_record["is_external"] and file_record.get("url_private_download"):
                        await self._download_file(file_obj, file_record, sync_job)

            # Mark conversation as synced. last_seen_ts is only advanced once the
            # whole conversation finished without raising: moving it on a partial
            # walk would permanently skip whatever the failure interrupted.
            conversation.last_synced = datetime.utcnow()
            conversation.last_seen_ts = newest_ts
            # A previous failure is resolved the moment a run succeeds — usually
            # because someone invited the bot. Leaving the message up would tell
            # them to fix something that is already fixed.
            conversation.last_error = None
            sync_cursor.is_complete = True
            sync_job.conversations_processed += 1

            logger.info(f"Completed conversation {conv_data.get('name', conv_id)}: {message_count} messages")

        except Exception as e:
            logger.error(f"Failed to sync conversation {conv_data.get('name', conv_data.get('id'))}: {e}")
            sync_job.errors.append(f"Conversation error: {e}")
            # Also record it against the conversation itself. The job-level list
            # says something failed; this says which channel and why, which is
            # what the user actually needs to act on.
            if conversation is not None:
                conversation.last_error = _slack_error_code(e)

    async def _filter_to_requested_conversations(self, conversations: list[dict]) -> list[dict]:
        """Narrow Slack's full conversation list down to the ones asked for.

        conversation_ids are local Conversation UUIDs, resolved to their Slack
        ids here — scoped by installation_id in the same query, so a UUID that
        does not belong to this installation is silently dropped rather than
        honoured. That is the entire tenant boundary for this feature: without
        this filter, one company's request could name another company's
        conversation UUID (both are just UUIDs on the wire) and have it synced.

        An id that does not resolve to anything (bad UUID, or genuinely
        belongs elsewhere) is dropped rather than raising, so one stale
        selection in a picker does not fail an otherwise-valid partial sync.
        """
        valid_uuids: list[UUID] = []
        for raw in self.conversation_ids:
            try:
                valid_uuids.append(UUID(raw))
            except (ValueError, AttributeError, TypeError):
                continue
        if not valid_uuids:
            return []

        rows = await self.db.scalars(
            select(Conversation).where(
                Conversation.installation_id == self.installation_id,
                Conversation.id.in_(valid_uuids),
            )
        )
        allowed_slack_ids = {row.slack_conversation_id for row in rows}
        return [c for c in conversations if c.get("id") in allowed_slack_ids]

    def _resolve_oldest(self, conversation: Conversation) -> str | None:
        """The Slack timestamp this conversation should be walked from.

        Two independent limits, and the later one wins:

        * the **date window** — how far back the user wants to look at all
        * the **incremental cursor** — what we already processed last time

        Taking the maximum means a 12-month window with a sync from yesterday
        asks Slack for one day, not twelve months. Returning None walks
        everything, which is the correct behaviour for a first sync with no
        window set.

        A full resync ignores the cursor but still respects the window: "fetch
        it all again" should not quietly mean "and also go further back than the
        user asked for".
        """
        window_start: float | None = None
        if self.sync_window_days:
            window_start = (
                datetime.now(timezone.utc) - timedelta(days=self.sync_window_days)
            ).timestamp()

        cursor_ts: float | None = None
        if not self.full_resync and conversation.last_seen_ts:
            cursor_ts = float(conversation.last_seen_ts)

        candidates = [value for value in (window_start, cursor_ts) if value is not None]
        if not candidates:
            return None
        return f"{max(candidates):.6f}"

    async def _download_file(self, file_obj: File, file_record: dict, sync_job: SyncJob) -> None:
        """Download and store a file, keeping the job's counters in step.

        sync_job is passed in so files_downloaded/files_failed advance per sync,
        the same way files_discovered and conversations_processed do. Without it
        both counters stayed at 0 for every run, so a fully successful sync
        reported "0 downloaded, 0 failed" while the files were on disk.
        """
        try:
            if not file_record.get("url_private_download"):
                file_obj.download_failed = True
                file_obj.download_error = "No download URL provided"
                sync_job.files_failed += 1
                return

            s3_key, sha256_hash = await self.download_manager.download_and_store(
                file_obj.slack_file_id,
                file_obj.filename,
                file_record["url_private_download"],
                self.bot_token,
            )

            # Verify S3 upload
            is_verified = await self.download_manager.verify_s3_file(s3_key, sha256_hash)
            if not is_verified:
                file_obj.download_failed = True
                file_obj.download_error = "S3 verification failed"
                sync_job.files_failed += 1
                logger.warning(f"S3 verification failed for {file_obj.slack_file_id}")
                return

            file_obj.s3_key = s3_key
            file_obj.sha256_hash = sha256_hash
            file_obj.downloaded = True
            sync_job.files_downloaded += 1

        except Exception as e:
            logger.error(f"Failed to download file {file_obj.slack_file_id}: {e}")
            file_obj.download_failed = True
            file_obj.download_error = str(e)
            sync_job.files_failed += 1

    async def _get_or_create_cursor(self, sync_job_id: UUID, conversation_id: UUID) -> SyncCursor:
        """Get existing cursor or create a new one."""
        existing = await self.db.scalar(
            select(SyncCursor).filter(
                SyncCursor.sync_job_id == sync_job_id,
                SyncCursor.conversation_id == conversation_id,
            )
        )

        if existing:
            return existing

        cursor = SyncCursor(sync_job_id=sync_job_id, conversation_id=conversation_id)
        self.db.add(cursor)
        await self.db.flush()
        return cursor


# Removed: update_sync_job_stats(). It recomputed files_downloaded/files_failed by
# counting every File row for the installation, which is cumulative across all syncs
# and so contradicts the per-sync meaning of files_discovered. It was never called,
# and wiring it up would have made a second sync report the first sync's downloads.
# The counters are now incremented in _download_file instead.
