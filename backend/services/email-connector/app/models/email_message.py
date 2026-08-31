import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class EmailMessage(Base):
    """One Gmail message with at least one attachment worth tracking.

    The Slack "Conversation" equivalent in shape (one row per source unit a
    sync walks), but flatter: Gmail has no channel/DM structure to group by,
    so every message belongs directly to the account. Deliberately does not
    store the message body — only what's needed to show a document's origin
    in the UI (see docs/email-connector-plan.md §9, "store only what you
    need").
    """

    __tablename__ = "email_message"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("email_account.id", ondelete="CASCADE"), nullable=False
    )
    # Unbounded, not a narrow VARCHAR — the same lesson learned from
    # provider_attachment_id (see EmailAttachment): Microsoft Graph message
    # ids are opaque tokens that can run well past a "should be enough"
    # guess, and this codebase has already hit that exact
    # StringDataRightTruncationError once with a Gmail id. Widened
    # proactively rather than waiting to reproduce the bug with Outlook.
    provider_message_id: Mapped[str] = mapped_column(String, nullable=False)
    thread_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(998), nullable=True)  # RFC 5322 header length cap
    from_address: Mapped[str | None] = mapped_column(String(320), nullable=True)
    from_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    to_address: Mapped[str | None] = mapped_column(String(320), nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    has_attachments: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Just the small set of headers used for display — never the body, and
    # never a truly "raw" full header dump.
    raw_headers_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    indexed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    attachments: Mapped[list["EmailAttachment"]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )
