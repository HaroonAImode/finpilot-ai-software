"""Adapts GmailClient's raw Gmail REST shapes to the provider-agnostic
MailProviderClient contract (app/services/providers/base.py) that
SyncOrchestrator drives. GmailClient itself is untouched — this is a thin
wrapper, not a rewrite, so gmail/client.py's own tests
(test_gmail_client_parsing.py) keep exercising the real wire format
directly.
"""
from typing import AsyncIterator

import httpx

from app.services.gmail.client import GmailClient, extract_headers, iter_attachment_parts
from app.services.rate_limiter import RateLimiter
from app.services.sync_support import parse_received_at

from app.services.providers.base import NormalizedAttachment, NormalizedMessage, ProviderHistoryExpired


class GmailProviderClient:
    def __init__(self, access_token: str, rate_limiter: RateLimiter):
        self._raw = GmailClient(access_token, rate_limiter)
        self.next_cursor: str | None = None

    async def __aenter__(self) -> "GmailProviderClient":
        await self._raw.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self._raw.__aexit__(exc_type, exc_val, exc_tb)

    async def get_profile(self) -> dict:
        profile = await self._raw.get_profile()
        return {"email_address": profile.get("emailAddress")}

    async def _capture_cursor_if_unset(self) -> None:
        # Captured before listing, on whichever path runs first — the same
        # timing Gmail always used: a watermark taken now means mail that
        # arrives *during* this sync is simply picked up by the next one,
        # never missed.
        if self.next_cursor is None:
            profile = await self._raw.get_profile()
            self.next_cursor = profile.get("historyId")

    async def list_message_ids_since(self, cursor: str) -> AsyncIterator[str]:
        await self._capture_cursor_if_unset()
        try:
            async for message_id in self._raw.list_new_message_ids_since(cursor):
                yield message_id
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise ProviderHistoryExpired(str(exc)) from exc
            raise

    async def list_message_ids_backfill(self, window_days: int) -> AsyncIterator[str]:
        await self._capture_cursor_if_unset()
        async for message_id in self._raw.list_message_ids(f"has:attachment newer_than:{window_days}d"):
            yield message_id

    async def get_message(self, message_id: str) -> NormalizedMessage:
        message = await self._raw.get_message(message_id)
        headers = extract_headers(message)
        attachments = [
            NormalizedAttachment(
                index=i, filename=part["filename"], mimetype=part.get("mimeType"),
                size=part.get("body", {}).get("size", 0),
                provider_ref=part["body"]["attachmentId"],
            )
            for i, part in enumerate(iter_attachment_parts(message.get("payload", {})))
        ]
        return NormalizedMessage(
            provider_message_id=message_id,
            thread_id=message.get("threadId"),
            subject=headers.get("Subject"),
            snippet=message.get("snippet"),
            received_at=parse_received_at(headers.get("Date")),
            raw_headers=headers,
            attachments=attachments,
        )

    async def get_attachment_bytes(self, message_id: str, attachment: NormalizedAttachment) -> bytes:
        return await self._raw.get_attachment_bytes(message_id, attachment.provider_ref)
