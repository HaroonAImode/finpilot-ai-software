"""Microsoft Graph mail client — Phase 5 (plan §11a). Covers the pieces with
no Gmail equivalent: Graph's flat JSON message shape (vs Gmail's nested MIME
tree), standard-alphabet base64 attachment bytes (Gmail uses the URL-safe
alphabet), and delta query's edge-consistent cursor (only known *after* a
full page walk, unlike Gmail's up-front historyId watermark — see
app/services/providers/base.py's MailProviderClient.next_cursor docstring).
"""
import base64

import httpx
import pytest

from app.services.outlook.client import OutlookProviderClient
from app.services.providers.base import NormalizedAttachment, ProviderHistoryExpired
from app.services.rate_limiter import RateLimiter


class _FakeResponse:
    def __init__(self, json_body: dict, status_code: int = 200):
        self._json_body = json_body
        self.status_code = status_code
        self.headers: dict = {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://graph.microsoft.com/v1.0/x")
            raise httpx.HTTPStatusError("error", request=request, response=httpx.Response(self.status_code, request=request))

    def json(self) -> dict:
        return self._json_body


class _QueuedAsyncClient:
    """Returns each queued response in order, one per GET call — Graph
    pagination and the delta protocol both require several sequential calls
    with different bodies, unlike a single-shot OAuth POST."""

    def __init__(self, responses: list[_FakeResponse]):
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def get(self, url, params=None, headers=None):
        self.calls.append({"url": url, "params": params, "headers": headers})
        return self._responses.pop(0)

    async def aclose(self) -> None:
        pass


@pytest.fixture
def rate_limiter() -> RateLimiter:
    return RateLimiter()


def _client_with(monkeypatch, client: OutlookProviderClient, fake: _QueuedAsyncClient) -> None:
    client.client = fake  # bypass __aenter__'s real httpx.AsyncClient


class TestGetProfile:
    @pytest.mark.asyncio
    async def test_prefers_the_mail_field(self) -> None:
        client = OutlookProviderClient("token", RateLimiter())
        client.client = _QueuedAsyncClient([_FakeResponse({"mail": "vendor@company.com", "userPrincipalName": "x@y.onmicrosoft.com"})])

        profile = await client.get_profile()

        assert profile == {"email_address": "vendor@company.com"}

    @pytest.mark.asyncio
    async def test_falls_back_to_user_principal_name(self) -> None:
        client = OutlookProviderClient("token", RateLimiter())
        client.client = _QueuedAsyncClient([_FakeResponse({"mail": None, "userPrincipalName": "vendor@outlook.com"})])

        profile = await client.get_profile()

        assert profile == {"email_address": "vendor@outlook.com"}


class TestListMessageIdsBackfill:
    @pytest.mark.asyncio
    async def test_builds_a_filter_from_the_window_and_paginates(self) -> None:
        client = OutlookProviderClient("token", RateLimiter())
        page1 = _FakeResponse({"value": [{"id": "M1"}, {"id": "M2"}], "@odata.nextLink": "https://graph.microsoft.com/v1.0/next-page"})
        page2 = _FakeResponse({"value": [{"id": "M3"}]})
        cursor_capture = _FakeResponse({"value": [], "@odata.deltaLink": "https://graph.microsoft.com/v1.0/delta?token=fresh"})
        fake = _QueuedAsyncClient([page1, page2, cursor_capture])
        client.client = fake

        ids = [mid async for mid in client.list_message_ids_backfill(30)]

        assert ids == ["M1", "M2", "M3"]
        first_call_params = fake.calls[0]["params"]
        assert "hasAttachments eq true" in first_call_params["$filter"]
        # Pagination follows the literal nextLink rather than reconstructing
        # a URL — Graph's link already carries its own $skiptoken.
        assert fake.calls[1]["url"] == "https://graph.microsoft.com/v1.0/next-page"

    @pytest.mark.asyncio
    async def test_captures_a_fresh_cursor_after_backfill_completes(self) -> None:
        """Unlike the incremental path, a backfill has no page walk of its
        own to harvest a deltaLink from — it has to ask for one separately,
        the Graph equivalent of Gmail's getProfile().historyId."""
        client = OutlookProviderClient("token", RateLimiter())
        page1 = _FakeResponse({"value": [{"id": "M1"}]})
        cursor_capture = _FakeResponse({"value": [], "@odata.deltaLink": "https://graph.microsoft.com/v1.0/delta?token=fresh"})
        client.client = _QueuedAsyncClient([page1, cursor_capture])

        async for _ in client.list_message_ids_backfill(180):
            pass

        assert client.next_cursor == "https://graph.microsoft.com/v1.0/delta?token=fresh"


class TestListMessageIdsSince:
    @pytest.mark.asyncio
    async def test_filters_to_messages_that_have_attachments_client_side(self) -> None:
        """Delta returns every changed message, not just ones with
        attachments — unlike Gmail's query-filtered listing, this has to
        happen here."""
        client = OutlookProviderClient("token", RateLimiter())
        page = _FakeResponse({
            "value": [
                {"id": "M1", "hasAttachments": True},
                {"id": "M2", "hasAttachments": False},
            ],
            "@odata.deltaLink": "https://graph.microsoft.com/v1.0/delta?token=next",
        })
        client.client = _QueuedAsyncClient([page])

        ids = [mid async for mid in client.list_message_ids_since("https://graph.microsoft.com/v1.0/delta?token=old")]

        assert ids == ["M1"]

    @pytest.mark.asyncio
    async def test_the_final_pages_deltalink_becomes_the_next_cursor(self) -> None:
        client = OutlookProviderClient("token", RateLimiter())
        page1 = _FakeResponse({"value": [{"id": "M1", "hasAttachments": True}], "@odata.nextLink": "https://graph.microsoft.com/v1.0/next"})
        page2 = _FakeResponse({"value": [], "@odata.deltaLink": "https://graph.microsoft.com/v1.0/delta?token=final"})
        client.client = _QueuedAsyncClient([page1, page2])

        async for _ in client.list_message_ids_since("https://graph.microsoft.com/v1.0/delta?token=old"):
            pass

        assert client.next_cursor == "https://graph.microsoft.com/v1.0/delta?token=final"

    @pytest.mark.asyncio
    async def test_a_410_is_translated_to_provider_history_expired(self) -> None:
        """Graph's documented "resync required" signal for a deltaLink too
        old to resolve — the incremental-sync counterpart of Gmail's
        history.list 404, both handled the same way by SyncOrchestrator."""
        client = OutlookProviderClient("token", RateLimiter())
        client.client = _QueuedAsyncClient([_FakeResponse({"error": {"code": "ResyncRequired"}}, status_code=410)])

        with pytest.raises(ProviderHistoryExpired):
            async for _ in client.list_message_ids_since("https://graph.microsoft.com/v1.0/delta?token=too-old"):
                pass

    @pytest.mark.asyncio
    async def test_a_non_410_error_is_not_swallowed(self) -> None:
        client = OutlookProviderClient("token", RateLimiter())
        client.client = _QueuedAsyncClient([_FakeResponse({"error": {"code": "InternalServerError"}}, status_code=500)])

        with pytest.raises(httpx.HTTPStatusError):
            async for _ in client.list_message_ids_since("https://graph.microsoft.com/v1.0/delta?token=x"):
                pass


class TestGetMessage:
    @pytest.mark.asyncio
    async def test_normalizes_from_subject_and_attachments(self) -> None:
        client = OutlookProviderClient("token", RateLimiter())
        client.client = _QueuedAsyncClient([_FakeResponse({
            "subject": "Invoice #1841",
            "receivedDateTime": "2026-02-20T10:15:30Z",
            "conversationId": "CONV1",
            "bodyPreview": "Please find attached...",
            "from": {"emailAddress": {"name": "Ayesha Khan", "address": "ayesha@vendor.com"}},
            "toRecipients": [{"emailAddress": {"address": "billing@company.com"}}],
            "attachments": [
                {"id": "AAA1", "name": "invoice.pdf", "contentType": "application/pdf", "size": 45000},
                {"id": "AAA2", "name": "receipt.jpg", "contentType": "image/jpeg", "size": 80000},
            ],
        })])

        message = await client.get_message("MSG1")

        assert message.provider_message_id == "MSG1"
        assert message.thread_id == "CONV1"
        assert message.subject == "Invoice #1841"
        assert message.raw_headers["From"] == "Ayesha Khan <ayesha@vendor.com>"
        assert message.raw_headers["To"] == "billing@company.com"
        assert message.received_at is not None and message.received_at.year == 2026
        assert [a.filename for a in message.attachments] == ["invoice.pdf", "receipt.jpg"]
        assert [a.index for a in message.attachments] == [0, 1]
        assert message.attachments[0].provider_ref == "AAA1"

    @pytest.mark.asyncio
    async def test_an_attachment_missing_id_or_name_is_skipped(self) -> None:
        """Defensive parity with Gmail's iter_attachment_parts, which skips
        anything without a real filename+fetchable id."""
        client = OutlookProviderClient("token", RateLimiter())
        client.client = _QueuedAsyncClient([_FakeResponse({
            "subject": "x", "attachments": [
                {"id": "AAA1", "name": "real.pdf", "contentType": "application/pdf", "size": 100},
                {"id": None, "name": "broken.pdf", "contentType": "application/pdf", "size": 100},
            ],
        })])

        message = await client.get_message("MSG1")

        assert [a.filename for a in message.attachments] == ["real.pdf"]

    @pytest.mark.asyncio
    async def test_a_sender_with_no_display_name_uses_the_bare_address(self) -> None:
        client = OutlookProviderClient("token", RateLimiter())
        client.client = _QueuedAsyncClient([_FakeResponse({
            "subject": "x", "from": {"emailAddress": {"address": "billing@vendor.com"}},
        })])

        message = await client.get_message("MSG1")

        assert message.raw_headers["From"] == "billing@vendor.com"


class TestGetAttachmentBytes:
    @pytest.mark.asyncio
    async def test_decodes_standard_base64_not_url_safe(self) -> None:
        """Graph uses the standard base64 alphabet — the opposite of
        Gmail's, which is URL-safe. Using the wrong decoder silently
        corrupts any content containing + or / bytes."""
        raw = b"pdf-bytes-with-/-and-+-chars"
        encoded = base64.b64encode(raw).decode()
        client = OutlookProviderClient("token", RateLimiter())
        client.client = _QueuedAsyncClient([_FakeResponse({"contentBytes": encoded})])

        content = await client.get_attachment_bytes("MSG1", NormalizedAttachment(index=0, filename="x.pdf", mimetype=None, size=len(raw), provider_ref="AAA1"))

        assert content == raw

    @pytest.mark.asyncio
    async def test_an_item_attachment_with_no_content_bytes_raises_clearly(self) -> None:
        """A forwarded email/calendar item (itemAttachment), not a file —
        has no contentBytes at all. Real invoices/receipts are always
        fileAttachments, so this is a clean "can't handle this" signal
        rather than a silent empty download."""
        client = OutlookProviderClient("token", RateLimiter())
        client.client = _QueuedAsyncClient([_FakeResponse({"name": "Forwarded meeting invite"})])

        with pytest.raises(ValueError, match="not a file attachment"):
            await client.get_attachment_bytes("MSG1", NormalizedAttachment(index=0, filename="x", mimetype=None, size=0, provider_ref="AAA1"))
