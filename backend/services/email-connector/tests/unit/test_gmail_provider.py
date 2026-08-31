"""GmailProviderClient — the thin adapter from GmailClient's raw Gmail
shapes to the provider-agnostic MailProviderClient contract SyncOrchestrator
now drives (Phase 5, plan §11a). GmailClient itself is untouched and stays
covered by test_gmail_client_parsing.py; this file only covers the adapter's
own logic: cursor-capture timing, 404 translation, and shape normalization.
"""
import httpx
import pytest

from app.services.gmail.provider import GmailProviderClient
from app.services.providers.base import ProviderHistoryExpired
from app.services.rate_limiter import RateLimiter


def _not_found_error() -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://gmail.googleapis.com/gmail/v1/users/me/history")
    return httpx.HTTPStatusError("not found", request=request, response=httpx.Response(404, request=request))


class _FakeRaw:
    """Stands in for the wrapped GmailClient — the adapter only ever calls
    these five methods on it."""

    def __init__(self, *, history_id="H100", incremental_ids=None, incremental_raises=None, backfill_ids=None):
        self._history_id = history_id
        self._incremental_ids = incremental_ids or []
        self._incremental_raises = incremental_raises
        self._backfill_ids = backfill_ids or []
        self.backfill_query: str | None = None
        self.profile_calls = 0

    async def get_profile(self) -> dict:
        self.profile_calls += 1
        return {"emailAddress": "vendor@company.com", "historyId": self._history_id}

    async def list_new_message_ids_since(self, cursor: str):
        if self._incremental_raises:
            raise self._incremental_raises
        for mid in self._incremental_ids:
            yield mid

    async def list_message_ids(self, query: str):
        self.backfill_query = query
        for mid in self._backfill_ids:
            yield mid

    async def get_message(self, message_id: str) -> dict:
        return {
            "threadId": "T1",
            "snippet": "...",
            "payload": {
                "headers": [
                    {"name": "From", "value": "Vendor <billing@vendor.com>"},
                    {"name": "Subject", "value": "Invoice"},
                    {"name": "Date", "value": "Wed, 19 Aug 2026 10:00:00 +0500"},
                ],
                "mimeType": "multipart/mixed",
                "parts": [
                    {"filename": "invoice.pdf", "mimeType": "application/pdf", "body": {"attachmentId": "ATT1", "size": 1000}},
                ],
            },
        }

    async def get_attachment_bytes(self, message_id: str, attachment_id: str) -> bytes:
        assert attachment_id == "ATT1"
        return b"pdf-bytes"


def _adapter(**kwargs) -> GmailProviderClient:
    client = GmailProviderClient("token", RateLimiter())
    client._raw = _FakeRaw(**kwargs)
    return client


class TestGetProfile:
    @pytest.mark.asyncio
    async def test_returns_just_the_email_address(self) -> None:
        client = _adapter()
        profile = await client.get_profile()
        assert profile == {"email_address": "vendor@company.com"}


class TestCursorCaptureTiming:
    @pytest.mark.asyncio
    async def test_the_watermark_is_captured_before_incremental_listing_starts(self) -> None:
        """Same reasoning Gmail always used: capturing "now" before scanning
        means mail arriving *during* this sync is picked up by the next run,
        never missed — not after, when it could already reflect messages
        this run itself is about to process."""
        client = _adapter(history_id="H200", incremental_ids=["M1"])

        _ = [mid async for mid in client.list_message_ids_since("H100")]

        assert client.next_cursor == "H200"

    @pytest.mark.asyncio
    async def test_the_watermark_is_still_captured_even_if_the_listing_then_404s(self) -> None:
        """The fallback-to-backfill path relies on this: a valid cursor must
        already be sitting in next_cursor by the time SyncOrchestrator
        catches ProviderHistoryExpired and moves on to backfill."""
        client = _adapter(history_id="H300", incremental_raises=_not_found_error())

        with pytest.raises(ProviderHistoryExpired):
            _ = [mid async for mid in client.list_message_ids_since("very-old")]

        assert client.next_cursor == "H300"

    @pytest.mark.asyncio
    async def test_backfill_also_captures_the_watermark(self) -> None:
        client = _adapter(history_id="H400", backfill_ids=["M1"])

        _ = [mid async for mid in client.list_message_ids_backfill(180)]

        assert client.next_cursor == "H400"
        assert client._raw.profile_calls == 1

    @pytest.mark.asyncio
    async def test_a_non_404_error_from_incremental_listing_is_not_swallowed(self) -> None:
        request = httpx.Request("GET", "https://gmail.googleapis.com/x")
        error = httpx.HTTPStatusError("server error", request=request, response=httpx.Response(500, request=request))
        client = _adapter(incremental_raises=error)

        with pytest.raises(httpx.HTTPStatusError):
            _ = [mid async for mid in client.list_message_ids_since("H100")]


class TestListMessageIdsBackfill:
    @pytest.mark.asyncio
    async def test_builds_gmails_search_query_from_the_window(self) -> None:
        client = _adapter(backfill_ids=[])

        _ = [mid async for mid in client.list_message_ids_backfill(45)]

        assert "has:attachment" in client._raw.backfill_query
        assert "newer_than:45d" in client._raw.backfill_query


class TestGetMessage:
    @pytest.mark.asyncio
    async def test_normalizes_headers_and_attachments(self) -> None:
        client = _adapter()

        message = await client.get_message("MSG1")

        assert message.provider_message_id == "MSG1"
        assert message.thread_id == "T1"
        assert message.subject == "Invoice"
        assert message.raw_headers["From"] == "Vendor <billing@vendor.com>"
        assert message.received_at is not None
        assert len(message.attachments) == 1
        assert message.attachments[0].filename == "invoice.pdf"
        assert message.attachments[0].provider_ref == "ATT1"
        assert message.attachments[0].index == 0


class TestGetAttachmentBytes:
    @pytest.mark.asyncio
    async def test_delegates_to_the_raw_client_with_the_normalized_attachments_provider_ref(self) -> None:
        from app.services.providers.base import NormalizedAttachment

        client = _adapter()
        attachment = NormalizedAttachment(index=0, filename="invoice.pdf", mimetype="application/pdf", size=1000, provider_ref="ATT1")

        content = await client.get_attachment_bytes("MSG1", attachment)

        assert content == b"pdf-bytes"
