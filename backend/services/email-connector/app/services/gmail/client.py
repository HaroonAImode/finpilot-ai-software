"""Gmail REST API client — message listing, message detail, attachment bytes,
and the two ways of finding "what's new": a query-based search for the first
sync, and history.list for every sync after that.
"""
import base64
import logging
from typing import AsyncIterator, Iterator, Optional

import httpx

from app.services.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

BASE_URL = "https://gmail.googleapis.com/gmail/v1/users/me"


class GmailClient:
    def __init__(self, access_token: str, rate_limiter: RateLimiter):
        self.access_token = access_token
        self.rate_limiter = rate_limiter
        self.client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "GmailClient":
        self.client = httpx.AsyncClient(timeout=30.0)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.client:
            await self.client.aclose()

    async def _call(self, method_bucket: str, path: str, params: Optional[dict] = None) -> dict:
        if self.client is None:
            raise RuntimeError("Client not initialized. Use 'async with' context manager.")

        await self.rate_limiter.acquire(method_bucket)

        response = await self.client.get(
            f"{BASE_URL}{path}",
            params=params or {},
            headers={"Authorization": f"Bearer {self.access_token}"},
        )

        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 1))
            logger.warning("Rate limited on %s. Retry after %ss", method_bucket, retry_after)
            self.rate_limiter.set_retry_after(method_bucket, retry_after)
            import asyncio

            await asyncio.sleep(retry_after)
            return await self._call(method_bucket, path, params)

        response.raise_for_status()
        return response.json()

    async def get_profile(self) -> dict:
        """Returns {emailAddress, historyId, messagesTotal, threadsTotal}."""
        return await self._call("getProfile", "/profile")

    async def list_message_ids(self, query: str) -> AsyncIterator[str]:
        """Search-based listing, for a first sync with no history cursor yet.

        `query` uses Gmail's search syntax (e.g. "has:attachment
        newer_than:180d") so the filtering happens at Gmail, not by fetching
        every message and discarding most of it here.
        """
        page_token = None
        while True:
            params = {"q": query, "maxResults": 100}
            if page_token:
                params["pageToken"] = page_token

            payload = await self._call("messages.list", "/messages", params)
            for message in payload.get("messages", []):
                yield message["id"]

            page_token = payload.get("nextPageToken")
            if not page_token:
                break

    async def list_new_message_ids_since(self, start_history_id: str) -> AsyncIterator[str]:
        """Incremental listing via Gmail's history API.

        Only messagesAdded records are used — a message that only had a label
        change (messagesAdded is absent, only labelsAdded/labelsRemoved
        present) is not a new document and would just waste a messages.get
        call re-processing something already synced.

        If Gmail responds 404 (the history id is too old — Gmail only
        retains ~30 days of history), the caller must fall back to a full
        backfill; this raises rather than silently yielding nothing so that
        fallback decision is visible at the call site, not buried here.
        """
        page_token = None
        while True:
            params = {"startHistoryId": start_history_id, "historyTypes": "messageAdded", "maxResults": 100}
            if page_token:
                params["pageToken"] = page_token

            payload = await self._call("history.list", "/history", params)
            for record in payload.get("history", []):
                for added in record.get("messagesAdded", []):
                    yield added["message"]["id"]

            page_token = payload.get("nextPageToken")
            if not page_token:
                break

    async def get_message(self, message_id: str) -> dict:
        """Full message resource: headers, snippet, and the MIME part tree
        that carries attachment metadata (filename/mimeType/size/attachmentId)."""
        return await self._call("messages.get", f"/messages/{message_id}", {"format": "full"})

    async def get_attachment_bytes(self, message_id: str, attachment_id: str) -> bytes:
        """Fetch one attachment's content.

        Note that `attachment_id` is **ephemeral**: Gmail mints a fresh
        attachmentId on every messages.get call for the very same
        attachment, so it is only valid alongside the message fetch it came
        from. Found live — a re-sync created a full duplicate set of rows
        because the dedup key was built on it (12 distinct ids for one
        unchanged file across 12 runs). Never persist it as an identity;
        it is a short-lived fetch handle, nothing more.
        """
        payload = await self._call(
            "attachments.get", f"/messages/{message_id}/attachments/{attachment_id}"
        )
        # Gmail returns the body as URL-safe base64, per RFC 4648 §5 — the
        # opposite alphabet from base64.b64decode.
        return base64.urlsafe_b64decode(payload["data"] + "=" * (-len(payload["data"]) % 4))


def extract_headers(message: dict) -> dict[str, str]:
    """Pulls just From/Subject/To/Date out of the header list — never the
    full header dump, per the "store only what you need" principle."""
    wanted = {"from", "subject", "to", "date"}
    headers = message.get("payload", {}).get("headers", [])
    return {h["name"]: h["value"] for h in headers if h.get("name", "").lower() in wanted}


def iter_attachment_parts(payload: dict) -> Iterator[dict]:
    """A plain (synchronous) generator. Walks the MIME part tree
    (parts can nest, e.g. multipart/mixed containing multipart/related)
    yielding every part that carries a real attachment: a filename and an
    attachmentId. Inline content without a filename (the HTML/plain-text
    message body itself) is skipped by the same check — it never has a
    filename, so there is no separate "is this the body" branch needed.

    Yields in stable document order (depth-first, parts in the order Gmail
    lists them). That order is load-bearing, not cosmetic: the caller
    numbers attachments by position to build a dedup key, because Gmail's
    own attachmentId cannot be used for that (see get_attachment_bytes).
    A received message's MIME structure is immutable, so the same message
    always produces the same sequence.
    """
    stack = [payload]
    while stack:
        part = stack.pop()
        body = part.get("body", {})
        if part.get("filename") and body.get("attachmentId"):
            yield part
        # Reversed because this is a LIFO stack — without it, sibling parts
        # come back in reverse order and the positional index below would
        # not match the order Gmail actually lists them in.
        stack.extend(reversed(part.get("parts", [])))
