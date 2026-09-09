import asyncio
import logging
from datetime import datetime
from typing import AsyncIterator, Optional
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models import AppUser, Conversation, ConversationType, File, SyncJob
from app.services.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


class DiscoveryService:
    """
    Discovers files in Slack conversations via conversation walk (Approach B).
    Walks: conversations.list → conversations.history → conversations.replies
    Extracts file metadata with full source linkage (channel/message/thread).
    """

    def __init__(self, bot_token: str, settings: Settings, rate_limiter: RateLimiter):
        self.bot_token = bot_token
        self.settings = settings
        self.rate_limiter = rate_limiter
        self.base_url = "https://slack.com/api"
        self.client = None

    async def __aenter__(self):
        self.client = httpx.AsyncClient(timeout=30.0)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.client:
            await self.client.aclose()

    async def _slack_call(self, method: str, params: Optional[dict] = None) -> dict:
        """Make a Slack API call with rate limiting."""
        if self.client is None:
            raise RuntimeError("Client not initialized. Use 'async with' context manager.")

        await self.rate_limiter.acquire(method)

        headers = {"Authorization": f"Bearer {self.bot_token}"}
        url = f"{self.base_url}/{method}"

        response = await self.client.get(url, params=params or {}, headers=headers)

        # Handle rate limiting
        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 1))
            logger.warning(f"Rate limited on {method}. Retry after {retry_after}s")
            self.rate_limiter.set_retry_after(method, retry_after)
            # Retry after backoff
            await asyncio.sleep(retry_after)
            return await self._slack_call(method, params)

        response.raise_for_status()
        payload = response.json()

        if not payload.get("ok"):
            error = payload.get("error", "unknown_error")
            logger.error(f"Slack API error on {method}: {error}")
            raise ValueError(f"Slack API error: {error}")

        return payload

    async def list_conversations(self) -> AsyncIterator[dict]:
        """
        List all conversations (channels, DMs, group DMs) the app has access to.
        Yields conversation objects.
        """
        cursor = None
        while True:
            params = {
                "limit": 100,
                "types": "public_channel,private_channel,im,mpim",
                "exclude_archived": True,
            }
            if cursor:
                params["cursor"] = cursor

            payload = await self._slack_call("conversations.list", params)
            for conversation in payload.get("channels", []):
                yield conversation

            cursor = payload.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break

    async def walk_conversation_messages(
        self, channel_id: str, oldest: Optional[str] = None
    ) -> AsyncIterator[tuple[dict, Optional[str]]]:
        """
        Walk through all messages in a conversation and their thread replies.

        Args:
            oldest: Slack timestamp. Only messages after it are returned, which is
                what makes both the date window and incremental sync possible —
                the filtering happens at Slack rather than by fetching everything
                and discarding most of it here.

        Yields:
            tuple: (message, thread_ts) where thread_ts is None for top-level messages
        """
        cursor = None
        while True:
            params = {
                "channel": channel_id,
                "limit": 100,
                # inclusive=True keeps the message *at* `oldest` in the results.
                # That is exactly wrong once `oldest` comes from an incremental
                # cursor: `oldest` is set to the newest message a previous sync
                # already fully processed, so including it re-fetches that one
                # message — and re-extracts its files — on every single run
                # forever. Found by running two incremental syncs back to back:
                # each channel with files re-reported "1 message" and the same
                # files as newly discovered, even though nothing had changed in
                # Slack. False (Slack's own default) makes `oldest` exclusive,
                # which is what "resume after what I already saw" means.
                "inclusive": False,
            }
            if oldest:
                params["oldest"] = oldest
            if cursor:
                params["cursor"] = cursor

            payload = await self._slack_call("conversations.history", params)

            for message in payload.get("messages", []):
                # Yield top-level message
                yield message, None

                # If message has threads, walk replies
                if message.get("reply_count", 0) > 0:
                    thread_ts = message.get("ts")
                    async for reply_msg in self._walk_thread_replies(channel_id, thread_ts):
                        yield reply_msg, thread_ts

            cursor = payload.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break

    async def _walk_thread_replies(self, channel_id: str, thread_ts: str) -> AsyncIterator[dict]:
        """Walk through all replies in a thread."""
        cursor = None
        while True:
            params = {
                "channel": channel_id,
                "ts": thread_ts,
                "limit": 100,
            }
            if cursor:
                params["cursor"] = cursor

            payload = await self._slack_call("conversations.replies", params)

            for message in payload.get("messages", []):
                # Skip the parent message (it was already yielded)
                if message.get("ts") != thread_ts:
                    yield message

            cursor = payload.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break

    async def extract_files_from_message(
        self,
        message: dict,
        channel_id: str,
        message_ts: str,
        thread_ts: Optional[str],
    ) -> list[dict]:
        """
        Extract file metadata from a message.
        Returns a list of file records with full source linkage.
        """
        files = []
        for file_obj in message.get("files", []):
            # Skip external files for download, but still track metadata.
            # Slack tombstones can appear as a synthetic file object with an id and
            # no permalink, so normalize the stored value to an empty string rather
            # than allowing None into the non-null `file.slack_permalink` column.
            file_record = {
                "slack_file_id": file_obj.get("id"),
                "channel_id": channel_id,
                "message_ts": message_ts,
                "thread_ts": thread_ts,
                "filename": file_obj.get("name") or "unknown",
                "title": file_obj.get("title"),
                "file_type": file_obj.get("pretty_type") or file_obj.get("filetype") or "unknown",
                "mimetype": file_obj.get("mimetype"),
                "size": file_obj.get("size", 0),
                "created_at": file_obj.get("created", message.get("ts")),
                "is_external": file_obj.get("is_external", False),
                "external_type": file_obj.get("external_type"),
                "url_private_download": file_obj.get("url_private_download"),
                "shared_by_user_id": message.get("user"),
                "shared_by_user_name": None,  # Will be looked up later
                "slack_permalink": (file_obj.get("permalink_public") or file_obj.get("permalink") or ""),
                "raw_json": file_obj,
            }
            files.append(file_record)
        return files

    async def enrich_user_info(self, user_id: str) -> dict:
        """Fetch user info for enriching file metadata."""
        try:
            payload = await self._slack_call("users.info", {"user": user_id})
            return payload.get("user", {})
        except Exception as e:
            logger.warning(f"Failed to fetch user info for {user_id}: {e}")
            return {}


# Service for managing discovery state and persisting files
class DiscoveryPersistenceService:
    """Persist discovered files and conversation state to database."""

    def __init__(self, db: AsyncSession, installation_id: str):
        self.db = db
        self.installation_id = installation_id
        self.user_cache = {}  # Cache for user lookups

    async def get_or_create_conversation(
        self, conv_data: dict, slack_team_id: str, discovery: "DiscoveryService | None" = None
    ) -> Conversation:
        """Get or create a conversation record.

        `discovery` is optional and used only to resolve a 1:1 DM's display
        name (see _resolve_dm_name below) — passing None just skips that
        lookup and falls back to "unknown", so this still works in any test or
        caller that has no live Slack client.
        """
        slack_conversation_id = conv_data.get("id")

        # Determine conversation type
        if conv_data.get("is_im"):
            conversation_type = ConversationType.im
        elif conv_data.get("is_mpim"):
            conversation_type = ConversationType.mpim
        elif conv_data.get("is_private"):
            conversation_type = ConversationType.private
        else:
            conversation_type = ConversationType.public

        # Try to find existing
        existing = await self.db.scalar(
            select(Conversation).where(
                (Conversation.installation_id == self.installation_id) &
                (Conversation.slack_conversation_id == slack_conversation_id)
            )
        )

        if existing:
            # Repair conversations created before this fix existed: a DM
            # stored as "unknown" is otherwise stuck that way forever, since
            # get_or_create_conversation only ever runs its create path once.
            if existing.name == "unknown" and conversation_type == ConversationType.im:
                resolved = await self._resolve_dm_name(conv_data, discovery)
                if resolved:
                    existing.name = resolved
            return existing

        name = conv_data.get("name")
        if not name and conversation_type == ConversationType.im:
            # Slack's conversations.list never includes a `name` for a 1:1 DM —
            # only a `user` id. Falling back to "unknown" made every DM section
            # in a "group files by conversation" view carry the same useless
            # label, which is exactly the case that view exists to avoid.
            name = await self._resolve_dm_name(conv_data, discovery)
        if not name:
            name = "unknown"

        # Create new
        conversation = Conversation(
            installation_id=self.installation_id,
            slack_conversation_id=slack_conversation_id,
            conversation_type=conversation_type,
            name=name,
            topic=conv_data.get("topic", {}).get("value"),
            description=conv_data.get("purpose", {}).get("value"),
        )
        self.db.add(conversation)
        await self.db.flush()
        return conversation

    async def _resolve_dm_name(
        self, conv_data: dict, discovery: "DiscoveryService | None"
    ) -> str | None:
        """The other person's name for a 1:1 DM, or None if it cannot be found.

        Costs one users.info call, but only the first time a DM is ever seen —
        every later sync hits the existing-conversation path above and never
        calls this again.
        """
        other_user_id = conv_data.get("user")
        if not other_user_id or discovery is None:
            return None
        try:
            profile = await discovery.enrich_user_info(other_user_id)
        except Exception:
            logger.warning("Could not resolve DM display name for user %s", other_user_id)
            return None
        return profile.get("real_name") or profile.get("name")

    async def get_or_create_user(self, user_id: str, user_data: Optional[dict] = None) -> AppUser:
        """Get or create a user record."""
        if user_id in self.user_cache:
            return self.user_cache[user_id]

        existing = await self.db.scalar(
            select(AppUser).where(
                (AppUser.installation_id == self.installation_id) &
                (AppUser.slack_user_id == user_id)
            )
        )

        if existing:
            self.user_cache[user_id] = existing
            return existing

        # Create new
        user_data = user_data or {}
        user = AppUser(
            installation_id=self.installation_id,
            slack_user_id=user_id,
            name=user_data.get("name") or user_data.get("real_name", "unknown"),
            email=user_data.get("profile", {}).get("email"),
            avatar_url=user_data.get("profile", {}).get("avatar_hash"),
            is_deleted=user_data.get("deleted", False),
        )
        self.db.add(user)
        await self.db.flush()
        self.user_cache[user_id] = user
        return user

    async def persist_file(
        self, file_record: dict, conversation_id: UUID, categorization_result: tuple[str, float, str]
    ) -> File:
        """Persist a discovered file to database."""
        category, confidence, category_source = categorization_result

        # Get or create the uploader user
        shared_by_user = None
        if file_record.get("shared_by_user_id"):
            shared_by_user = await self.get_or_create_user(
                file_record["shared_by_user_id"],
            )

        # Convert unix timestamp to datetime
        created_at = file_record.get("created_at")
        if isinstance(created_at, (int, float)):
            created_at = datetime.utcfromtimestamp(created_at)
        elif isinstance(created_at, str):
            try:
                # Handle unix timestamp as string
                created_at = datetime.utcfromtimestamp(float(created_at))
            except (ValueError, TypeError):
                created_at = datetime.utcnow()

        # A Slack file keeps its id across syncs, so re-syncing must update the
        # existing row. Inserting unconditionally (as this did) duplicated every
        # file on every sync: a second run over 23 files left 46 rows, all shown
        # twice in the browser.
        existing = await self.db.scalar(
            select(File).filter(
                File.installation_id == self.installation_id,
                File.slack_file_id == file_record["slack_file_id"],
            )
        )

        normalized_permalink = file_record.get("slack_permalink") or ""

        if existing is not None:
            existing.conversation_id = conversation_id
            existing.message_ts = file_record["message_ts"]
            existing.thread_ts = file_record["thread_ts"]
            existing.filename = file_record["filename"]
            existing.title = file_record.get("title")
            existing.file_type = file_record["file_type"]
            existing.mimetype = file_record.get("mimetype")
            existing.size = file_record["size"]
            existing.created_at = created_at
            existing.is_external = file_record["is_external"]
            existing.external_type = file_record.get("external_type")
            existing.url_private_download = file_record.get("url_private_download")
            if shared_by_user is not None:
                existing.shared_by_user_id = shared_by_user.id
            existing.shared_by_user_name = file_record.get("shared_by_user_name")
            existing.slack_permalink = normalized_permalink
            existing.raw_json = file_record.get("raw_json")
            # A human's correction outranks the classifier: re-running a sync must
            # not silently revert a category someone fixed by hand.
            if existing.category_source != "manual_override":
                existing.category = category
                existing.category_confidence = confidence
                existing.category_source = category_source
            await self.db.flush()
            return existing

        file_obj = File(
            installation_id=self.installation_id,
            slack_file_id=file_record["slack_file_id"],
            conversation_id=conversation_id,
            message_ts=file_record["message_ts"],
            thread_ts=file_record["thread_ts"],
            filename=file_record["filename"],
            title=file_record.get("title"),
            file_type=file_record["file_type"],
            mimetype=file_record.get("mimetype"),
            size=file_record["size"],
            created_at=created_at,
            is_external=file_record["is_external"],
            external_type=file_record.get("external_type"),
            url_private_download=file_record.get("url_private_download"),
            shared_by_user_id=shared_by_user.id if shared_by_user else None,
            shared_by_user_name=file_record.get("shared_by_user_name"),
            slack_permalink=normalized_permalink,
            category=category,
            category_confidence=confidence,
            category_source=category_source,
            raw_json=file_record.get("raw_json"),
        )
        self.db.add(file_obj)
        await self.db.flush()
        return file_obj
