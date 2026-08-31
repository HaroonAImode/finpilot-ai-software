"""Upsert helpers for EmailMessage/EmailAttachment — the same dedup shape as
slack-connector's DiscoveryPersistenceService, adapted to Gmail's ids.
"""
from datetime import datetime
from email.utils import parseaddr
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EmailAttachment, EmailMessage, ReviewStatus


def parse_from_header(from_header: str | None) -> tuple[str | None, str | None]:
    """Splits 'Ayesha Khan <ayesha@company.com>' into (name, address). Falls
    back to (None, None) for an empty/missing header rather than raising —
    a message with a malformed From header should still sync, just without
    a display name."""
    if not from_header:
        return None, None
    name, address = parseaddr(from_header)
    return (name or None), (address or None)


class EmailPersistenceService:
    def __init__(self, db: AsyncSession, account_id: UUID):
        self.db = db
        self.account_id = account_id

    async def get_or_create_message(
        self,
        provider_message_id: str,
        *,
        headers: dict,
        snippet: str | None,
        received_at: datetime | None,
        thread_id: str | None,
    ) -> EmailMessage:
        existing = await self.db.scalar(
            select(EmailMessage).where(
                EmailMessage.account_id == self.account_id,
                EmailMessage.provider_message_id == provider_message_id,
            )
        )
        if existing is not None:
            return existing

        from_name, from_address = parse_from_header(headers.get("From"))
        _, to_address = parse_from_header(headers.get("To"))

        message = EmailMessage(
            account_id=self.account_id,
            provider_message_id=provider_message_id,
            thread_id=thread_id,
            subject=headers.get("Subject"),
            from_address=from_address,
            from_name=from_name,
            to_address=to_address,
            received_at=received_at,
            has_attachments=True,
            snippet=snippet,
            raw_headers_json=headers,
        )
        self.db.add(message)
        await self.db.flush()
        return message

    async def persist_attachment(
        self,
        message_id: UUID,
        attachment_record: dict,
        categorization_result: tuple[str, float, str],
    ) -> EmailAttachment:
        category, confidence, category_source = categorization_result

        # Keyed on the attachment's *position* in its message, never on
        # Gmail's attachmentId. A received message's MIME structure is
        # immutable, so position is stable across syncs; attachmentId is
        # not — Gmail mints a new one on every fetch, which made the second
        # sync insert a full duplicate set of rows (found live: one
        # unchanged file had 12 distinct ids across 12 runs).
        existing = await self.db.scalar(
            select(EmailAttachment).where(
                EmailAttachment.account_id == self.account_id,
                EmailAttachment.message_id == message_id,
                EmailAttachment.attachment_index == attachment_record["attachment_index"],
            )
        )

        if existing is not None:
            existing.filename = attachment_record["filename"]
            existing.mimetype = attachment_record.get("mimetype")
            existing.size = attachment_record["size"]
            # Refreshed every run precisely because it expires — the stored
            # one is stale the moment the run that fetched it ends.
            existing.provider_attachment_id = attachment_record["provider_attachment_id"]
            # A human's correction outranks the classifier — a re-sync must
            # never silently revert a category someone fixed by hand.
            if existing.category_source != "manual_override":
                existing.category = category
                existing.category_confidence = confidence
                existing.category_source = category_source
            # review_status is deliberately NOT recomputed on re-sync. Once a
            # human has approved or rejected something, a later sync must not
            # quietly undo that decision — and re-deriving it would, since the
            # classifier's confidence has not changed. New sender rules apply
            # to newly-seen attachments; existing verdicts stand.
            await self.db.flush()
            return existing

        attachment = EmailAttachment(
            account_id=self.account_id,
            message_id=message_id,
            attachment_index=attachment_record["attachment_index"],
            provider_attachment_id=attachment_record["provider_attachment_id"],
            filename=attachment_record["filename"],
            mimetype=attachment_record.get("mimetype"),
            size=attachment_record["size"],
            category=category,
            category_confidence=confidence,
            category_source=category_source,
            review_status=attachment_record.get("review_status", ReviewStatus.imported),
            review_reason=attachment_record.get("review_reason"),
        )
        self.db.add(attachment)
        await self.db.flush()
        return attachment
