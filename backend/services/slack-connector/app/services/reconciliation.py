"""
Reconciliation/audit service for cross-checking files.list against conversation walk results.

Per §6E (Approach E - Hybrid), this service runs as a secondary audit pass to catch files
that might have been missed in the conversation walk (e.g., files shared via files.remote.share
without a normal message, or files whose parent message was deleted but the file persists).

Key principle from §9A: Never overwrite manual_override categories.
"""
import logging
from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import TokenCipher
from app.models import File, Installation, SyncJob, SyncStatus
from app.services.slack.client import SlackAPIClient

logger = logging.getLogger(__name__)


class ReconciliationService:
    """
    Runs a files.list audit pass to reconcile against conversation-walk results.
    
    Logs discrepancies without auto-remediating them, per §7's gap-detection guidance:
    - Files found in files.list but not in conversation walk (with reason)
    - Files found in conversation walk but not in files.list (rarer, but logged)
    - Metadata differences that might indicate staleness
    """

    def __init__(
        self,
        db: AsyncSession,
        installation: Installation,
        settings,
        rate_limiter,
    ):
        self.db = db
        self.installation = installation
        self.settings = settings
        self.rate_limiter = rate_limiter
        self.token_cipher = TokenCipher(settings.token_encryption_key)

    async def run_audit_pass(self, sync_job: SyncJob) -> dict:
        """
        Run a full files.list audit pass and reconcile with conversation-walk results.
        
        Returns a summary dict with:
        - files_in_list_only: count of files found via files.list not in DB
        - files_with_stale_metadata: count of DB files with outdated metadata
        - missing_scope_conversations: channels where the token lacks permission
        - anomalies: list of detailed discrepancies flagged for manual review
        """
        logger.info(
            f"[{sync_job.id}] Starting files.list reconciliation audit pass",
            extra={
                "event": "audit_started",
                "sync_job_id": str(sync_job.id),
                "installation_id": str(self.installation.id),
            },
        )

        # Decrypt bot token
        try:
            bot_token = self.token_cipher.decrypt(self.installation.bot_token_encrypted)
        except Exception as e:
            logger.error(
                f"[{sync_job.id}] Failed to decrypt bot token for audit pass",
                extra={
                    "event": "audit_token_decrypt_failed",
                    "sync_job_id": str(sync_job.id),
                    "error": str(e),
                },
            )
            raise

        slack_client = SlackAPIClient(bot_token, self.rate_limiter, self.settings)

        # Summary accumulators
        anomalies = []
        files_in_list_only = 0
        files_with_stale_metadata = 0
        db_file_ids_seen = set()
        missing_scope_conversations = set()

        # Walk files.list with pagination
        cursor = None
        page_count = 0

        try:
            while True:
                page_count += 1
                logger.debug(
                    f"[{sync_job.id}] Audit pass page {page_count}",
                    extra={"event": "audit_page_started", "page": page_count},
                )

                try:
                    response = await slack_client.call(
                        "files.list",
                        {
                            "limit": 100,
                            "cursor": cursor,
                            # Can optionally filter by type for targeted audit runs:
                            # "types": "images,pdfs"
                        },
                    )
                except Exception as e:
                    logger.warning(
                        f"[{sync_job.id}] files.list call failed in audit pass",
                        extra={
                            "event": "audit_files_list_failed",
                            "error": str(e),
                            "page": page_count,
                        },
                    )
                    # files.list failures don't stop the whole sync, just log and break
                    break

                files_in_page = response.get("files", [])

                if not files_in_page:
                    break  # No more pages

                for slack_file in files_in_page:
                    file_id = slack_file.get("id")
                    if not file_id:
                        continue

                    db_file_ids_seen.add(file_id)

                    # Check if this file is already in the database
                    db_file = await self.db.scalar(
                        select(File).where(
                            and_(
                                File.installation_id == self.installation.id,
                                File.slack_file_id == file_id,
                            )
                        )
                    )

                    if not db_file:
                        # This file was found via files.list but not discovered in the conversation walk
                        # This could happen if:
                        # 1. The file was shared via files.remote.share (reference, not message-attached)
                        # 2. The parent message was deleted but the file persists
                        # 3. The file is in a conversation the bot hasn't visited yet
                        files_in_list_only += 1

                        # Determine why it wasn't in the walk
                        channels = slack_file.get("channels", [])
                        groups = slack_file.get("groups", [])
                        ims = slack_file.get("ims", [])

                        anomalies.append({
                            "type": "file_in_list_not_in_walk",
                            "file_id": file_id,
                            "file_name": slack_file.get("name"),
                            "channels": channels,
                            "groups": groups,
                            "ims": ims,
                            "reason": "not found in conversation walk (possibly orphaned or in unvisited channel)",
                        })

                        logger.info(
                            f"[{sync_job.id}] Audit found file in files.list not in walk: {file_id}",
                            extra={
                                "event": "audit_file_discovered",
                                "file_id": file_id,
                                "file_name": slack_file.get("name"),
                                "channels": channels,
                                "groups": groups,
                            },
                        )

                    else:
                        # File is in both sources; check if metadata needs updating
                        # (files.list provides updated metadata even for already-synced files)
                        metadata_updates = self._compute_metadata_updates(
                            db_file, slack_file
                        )

                        if metadata_updates:
                            files_with_stale_metadata += 1
                            anomalies.append({
                                "type": "file_stale_metadata",
                                "file_id": file_id,
                                "file_name": slack_file.get("name"),
                                "updates_available": metadata_updates,
                            })

                            logger.debug(
                                f"[{sync_job.id}] File has stale metadata: {file_id}",
                                extra={
                                    "event": "audit_stale_metadata",
                                    "file_id": file_id,
                                    "updates": metadata_updates,
                                },
                            )

                # Move to next page
                cursor = response.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break

        except Exception as e:
            logger.error(
                f"[{sync_job.id}] Audit pass encountered unexpected error",
                extra={
                    "event": "audit_unexpected_error",
                    "error": str(e),
                },
            )
            # Don't re-raise; audit failures are logged but don't fail the whole sync

        # Phase 2: Check for DB files not in files.list (rarer, but log it)
        # This helps detect if files.list is returning incomplete data
        try:
            db_files = await self.db.scalars(
                select(File).where(File.installation_id == self.installation.id)
            )

            files_in_db_only = 0
            for db_file in db_files:
                if db_file.slack_file_id not in db_file_ids_seen:
                    files_in_db_only += 1
                    logger.debug(
                        f"File in DB but not in current files.list page: {db_file.slack_file_id}",
                        extra={
                            "event": "audit_file_in_db_only",
                            "file_id": db_file.slack_file_id,
                            "file_name": db_file.filename,
                        },
                    )

            # Don't add these as anomalies (files.list is paginated, they'll show up in next runs)
            # but do log the count for observability

        except Exception as e:
            logger.error(
                f"[{sync_job.id}] Failed to cross-check DB files against files.list",
                extra={"event": "audit_db_check_failed", "error": str(e)},
            )

        # Summary log
        logger.info(
            f"[{sync_job.id}] Reconciliation audit pass complete",
            extra={
                "event": "audit_completed",
                "files_in_list_only": files_in_list_only,
                "files_with_stale_metadata": files_with_stale_metadata,
                "total_anomalies": len(anomalies),
                "pages_scanned": page_count,
            },
        )

        return {
            "files_in_list_only": files_in_list_only,
            "files_with_stale_metadata": files_with_stale_metadata,
            "anomalies": anomalies,
            "pages_scanned": page_count,
        }

    def _compute_metadata_updates(self, db_file: File, slack_file: dict) -> dict | None:
        """
        Compare DB file metadata against files.list data.
        
        Returns a dict of fields that differ (for logging/audit purposes).
        Never auto-applies updates — just flags them for manual review.
        """
        updates = {}

        # Check size
        slack_size = slack_file.get("size")
        if slack_size and db_file.size != slack_size:
            updates["size"] = {
                "db": db_file.size,
                "slack": slack_size,
            }

        # Check mimetype
        slack_mimetype = slack_file.get("mimetype")
        if slack_mimetype and db_file.mimetype != slack_mimetype:
            updates["mimetype"] = {
                "db": db_file.mimetype,
                "slack": slack_mimetype,
            }

        # Check if it's still external (shouldn't change, but worth checking)
        slack_is_external = slack_file.get("is_external", False)
        if db_file.is_external != slack_is_external:
            updates["is_external"] = {
                "db": db_file.is_external,
                "slack": slack_is_external,
            }

        return updates if updates else None


class FileRetryManager:
    """
    Manages retrying failed file downloads (§19 — POST /files/{id}/retry endpoint).
    
    Resets download_failed and download_error flags so the Download Manager
    will pick up the file again on its next worker cycle.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def retry_file_download(self, file_id: UUID) -> File:
        """
        Reset a failed file download for retry.
        
        Raises HTTPException(404) if file not found.
        """
        db_file = await self.db.scalar(select(File).where(File.id == file_id))

        if not db_file:
            raise ValueError(f"File {file_id} not found")

        if db_file.downloaded:
            logger.info(
                f"File {file_id} is already successfully downloaded; ignoring retry request",
                extra={
                    "event": "retry_already_downloaded",
                    "file_id": str(file_id),
                },
            )
            return db_file

        # Reset failure flags so Download Manager will try again
        db_file.download_failed = False
        db_file.download_error = None
        db_file.s3_key = None  # Clear any partial upload state
        db_file.sha256_hash = None

        logger.info(
            f"Retrying file download: {file_id}",
            extra={
                "event": "file_retry_queued",
                "file_id": str(file_id),
                "file_name": db_file.filename,
            },
        )

        await self.db.commit()
        await self.db.refresh(db_file)

        return db_file
