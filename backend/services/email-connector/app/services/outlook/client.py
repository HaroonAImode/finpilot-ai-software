"""Microsoft Graph mail client — implements MailProviderClient
(app/services/providers/base.py) directly, since Graph's JSON is already
close to a flat "message + attachment list" shape and gains nothing from a
separate raw layer the way Gmail's nested MIME tree did.

Two structural differences from Gmail worth calling out:

- **Attachment bytes are metadata-separable, not always inline.** Graph's
  message resource can $expand=attachments, but a $select on that expansion
  excludes contentBytes — so, like Gmail, discovery (get_message) never
  downloads bytes; a attachment must be re-fetched by id to get
  contentBytes. This is what keeps the "needs_review" privacy design (plan
  §5b) working identically for both providers: bytes are never fetched
  until something is actually approved for download.

- **Incremental sync is edge-consistent by construction.** Gmail's
  historyId is a watermark taken *before* scanning. Graph's delta query
  instead returns its own forward cursor (@odata.deltaLink) only once a
  full page walk completes, and that walk is guaranteed consistent with
  whatever changed during it — there is no equivalent "capture cursor
  first" step needed or possible. See base.py's MailProviderClient.next_cursor
  docstring for the full reasoning.
"""
import asyncio
import base64
import logging
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator, Optional

import httpx

from app.services.providers.base import NormalizedAttachment, NormalizedMessage, ProviderHistoryExpired
from app.services.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

BASE_URL = "https://graph.microsoft.com/v1.0"
INBOX_MESSAGES_PATH = "/me/mailFolders/inbox/messages"


def _parse_graph_datetime(value: Optional[str]) -> Optional[datetime]:
    """Graph timestamps are ISO 8601 UTC ("2026-02-20T10:15:30Z"), a
    different wire format from Gmail's RFC 2822 Date header — this is why
    NormalizedMessage carries `received_at` pre-parsed rather than asking a
    shared header parser to understand two incompatible date formats."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class OutlookProviderClient:
    def __init__(self, access_token: str, rate_limiter: RateLimiter):
        self.access_token = access_token
        self.rate_limiter = rate_limiter
        self.client: httpx.AsyncClient | None = None
        self.next_cursor: str | None = None

    async def __aenter__(self) -> "OutlookProviderClient":
        self.client = httpx.AsyncClient(timeout=30.0)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.client:
            await self.client.aclose()

    async def _request(self, bucket: str, url: str, params: Optional[dict]) -> dict:
        if self.client is None:
            raise RuntimeError("Client not initialized. Use 'async with' context manager.")

        await self.rate_limiter.acquire(bucket)

        response = await self.client.get(
            url, params=params or {}, headers={"Authorization": f"Bearer {self.access_token}"}
        )

        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 1))
            logger.warning("Rate limited on %s. Retry after %ss", bucket, retry_after)
            self.rate_limiter.set_retry_after(bucket, retry_after)
            await asyncio.sleep(retry_after)
            return await self._request(bucket, url, params)

        response.raise_for_status()
        return response.json()

    async def _call(self, bucket: str, path: str, params: Optional[dict] = None) -> dict:
        return await self._request(bucket, f"{BASE_URL}{path}", params)

    async def _call_url(self, bucket: str, url: str) -> dict:
        """For paging via @odata.nextLink/@odata.deltaLink, which Graph
        returns as full absolute URLs, not relative paths."""
        return await self._request(bucket, url, None)

    async def get_profile(self) -> dict:
        payload = await self._call("me", "/me", {"$select": "mail,userPrincipalName"})
        return {"email_address": payload.get("mail") or payload.get("userPrincipalName")}

    async def _capture_fresh_cursor(self) -> None:
        """The Graph equivalent of Gmail's getProfile().historyId: a delta
        query with $deltatoken=latest returns immediately with just a
        deltaLink, no items — a cheap way to get "everything from now on"
        without walking the whole mailbox first. Used after a backfill,
        where (unlike the incremental path) there is no page walk of our
        own to harvest a deltaLink from."""
        payload = await self._call(
            "messages.delta", f"{INBOX_MESSAGES_PATH}/delta", {"$deltatoken": "latest"}
        )
        self.next_cursor = payload.get("@odata.deltaLink")

    async def list_message_ids_since(self, cursor: str) -> AsyncIterator[str]:
        """`cursor` is a full deltaLink URL persisted from a previous run."""
        next_url: str | None = cursor
        while next_url:
            try:
                payload = await self._call_url("messages.delta", next_url)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 410:
                    # Graph's documented "resync required" signal for a
                    # deltaLink too old to resolve — the incremental-sync
                    # counterpart of Gmail's history.list 404.
                    raise ProviderHistoryExpired(str(exc)) from exc
                raise

            # Delta returns every changed message, not just ones with
            # attachments (unlike Gmail's query-filtered listing) — the
            # attachment filter has to happen here, client-side.
            for item in payload.get("value", []):
                if item.get("hasAttachments"):
                    yield item["id"]

            next_url = payload.get("@odata.nextLink")
            if not next_url:
                self.next_cursor = payload.get("@odata.deltaLink")

    async def list_message_ids_backfill(self, window_days: int) -> AsyncIterator[str]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        params = {
            "$filter": f"hasAttachments eq true and receivedDateTime ge {cutoff}",
            "$select": "id",
            "$top": 50,
        }
        payload = await self._call("messages.list", INBOX_MESSAGES_PATH, params)
        while True:
            for item in payload.get("value", []):
                yield item["id"]
            next_url = payload.get("@odata.nextLink")
            if not next_url:
                break
            payload = await self._call_url("messages.list", next_url)

        await self._capture_fresh_cursor()

    async def get_message(self, message_id: str) -> NormalizedMessage:
        payload = await self._call(
            "messages.get",
            f"/me/messages/{message_id}",
            {
                "$select": "subject,receivedDateTime,conversationId,bodyPreview,from,toRecipients",
                "$expand": "attachments($select=id,name,contentType,size)",
            },
        )

        from_email = (payload.get("from") or {}).get("emailAddress") or {}
        from_address = from_email.get("address")
        from_name = from_email.get("name")
        to_recipients = payload.get("toRecipients") or []
        to_address = (to_recipients[0].get("emailAddress") or {}).get("address") if to_recipients else None

        raw_headers: dict[str, str] = {}
        if from_address:
            raw_headers["From"] = f"{from_name} <{from_address}>" if from_name else from_address
        if payload.get("subject"):
            raw_headers["Subject"] = payload["subject"]
        if to_address:
            raw_headers["To"] = to_address

        attachments = [
            NormalizedAttachment(
                index=i, filename=att["name"], mimetype=att.get("contentType"),
                size=att.get("size", 0), provider_ref=att["id"],
            )
            for i, att in enumerate(payload.get("attachments", []))
            if att.get("id") and att.get("name")
        ]

        return NormalizedMessage(
            provider_message_id=message_id,
            thread_id=payload.get("conversationId"),
            subject=payload.get("subject"),
            snippet=payload.get("bodyPreview"),
            received_at=_parse_graph_datetime(payload.get("receivedDateTime")),
            raw_headers=raw_headers,
            attachments=attachments,
        )

    async def get_attachment_bytes(self, message_id: str, attachment: NormalizedAttachment) -> bytes:
        payload = await self._call(
            "attachments.get",
            f"/me/messages/{message_id}/attachments/{attachment.provider_ref}",
            {"$select": "contentBytes"},
        )
        content_bytes = payload.get("contentBytes")
        if content_bytes is None:
            # An itemAttachment (a forwarded email/calendar item, not a
            # file) has no contentBytes. Real invoices/receipts are always
            # fileAttachments — this is a clean "can't handle this kind"
            # signal, not a transport failure, so it is raised rather than
            # silently returning empty bytes.
            raise ValueError(
                f"Attachment {attachment.provider_ref} on message {message_id} is not a file "
                "attachment (no retrievable content)"
            )
        # Standard base64 alphabet — unlike Gmail, Graph does not use the
        # URL-safe variant here.
        return base64.b64decode(content_bytes)
