"""On-demand re-fetch of one attachment's bytes from the mailbox provider.

Shared by /files/{id}/approve and /files/{id}/retry — both need the same
thing: download an attachment whose bytes were never stored (needs_review)
or failed to store (download_failed), right now, outside a sync run.

Branches once on account.provider rather than going through the
MailProviderClient abstraction sync_orchestrator.py uses: each branch is a
handful of lines, and the two providers' expiring-id stories are different
enough (Gmail's attachmentId is provably ephemeral, so this always
re-fetches the whole message first; Graph's attachment id is normally
stable, so it can be fetched directly) that forcing them through one shape
would not actually remove any duplication — see
app/services/providers/base.py's module docstring for the same reasoning
applied to auth.py and token_manager.
"""
from fastapi import HTTPException

from app.core.config import Settings
from app.models import EmailAccount, EmailAttachment, EmailMessage, EmailProviderName
from app.services.download_manager import DownloadManager
from app.services.gmail.client import GmailClient, iter_attachment_parts
from app.services.outlook.client import OutlookProviderClient
from app.services.providers.token_manager import TokenRefreshError, get_valid_access_token
from app.services.rate_limiter import get_rate_limiter


class AttachmentFetchError(HTTPException):
    """A 502 carrying a message specific enough to act on — distinguished
    from a plain HTTPException only so callers can catch this one type
    without also swallowing unrelated 502s."""

    def __init__(self, detail: str):
        super().__init__(status_code=502, detail=detail)


async def _refetch_from_gmail(access_token: str, message: EmailMessage, attachment: EmailAttachment) -> tuple[bytes, str]:
    """Gmail's attachmentId is ephemeral (see
    docs/email-connector-plan.md §5a.1), so the stored one from the original
    sync can never be reused here — the message has to be re-fetched to get
    a live id, and the attachment's stored attachment_index is what locates
    the right part within it."""
    async with GmailClient(access_token, get_rate_limiter()) as gmail:
        live_message = await gmail.get_message(message.provider_message_id)
        parts = list(iter_attachment_parts(live_message.get("payload", {})))
        if attachment.attachment_index >= len(parts):
            raise AttachmentFetchError("This attachment is no longer present in the email.")

        part = parts[attachment.attachment_index]
        live_id = part.get("body", {}).get("attachmentId")
        if not live_id:
            raise AttachmentFetchError("Gmail did not return a usable attachment id")

        content = await gmail.get_attachment_bytes(message.provider_message_id, live_id)
        return content, live_id


async def _refetch_from_outlook(access_token: str, message: EmailMessage, attachment: EmailAttachment) -> tuple[bytes, str]:
    async with OutlookProviderClient(access_token, get_rate_limiter()) as outlook:
        normalized = await outlook.get_message(message.provider_message_id)
        if attachment.attachment_index >= len(normalized.attachments):
            raise AttachmentFetchError("This attachment is no longer present in the email.")

        part = normalized.attachments[attachment.attachment_index]
        content = await outlook.get_attachment_bytes(message.provider_message_id, part)
        return content, part.provider_ref


async def refetch_and_store(
    settings: Settings, account: EmailAccount, message: EmailMessage, attachment: EmailAttachment,
) -> None:
    """Downloads `attachment` from its provider right now and mutates it in
    place (s3_key, sha256_hash, downloaded, provider_attachment_id). Does
    not commit — callers persist alongside whatever else the request changes.
    """
    try:
        access_token = await get_valid_access_token(account, settings)
    except TokenRefreshError as exc:
        # get_valid_access_token already set needs_reauth on `account`; the
        # caller's commit persists that. Re-raised as the same error type so
        # the UI's "reconnect" messaging is consistent everywhere it appears.
        raise AttachmentFetchError(str(exc)) from exc

    try:
        if account.provider == EmailProviderName.outlook:
            content, live_id = await _refetch_from_outlook(access_token, message, attachment)
        else:
            content, live_id = await _refetch_from_gmail(access_token, message, attachment)
    except AttachmentFetchError:
        raise
    except Exception as exc:
        raise AttachmentFetchError(f"Could not fetch this attachment from {account.provider.value}") from exc

    download_manager = DownloadManager(settings)
    s3_key, sha256_hash = await download_manager.store_bytes(
        str(attachment.id), attachment.filename, content
    )
    attachment.s3_key = s3_key
    attachment.sha256_hash = sha256_hash
    attachment.downloaded = True
    attachment.download_failed = False
    attachment.download_error = None
    attachment.provider_attachment_id = live_id
