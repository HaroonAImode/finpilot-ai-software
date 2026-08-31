import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Integer, String, func
from sqlalchemy.dialects.postgresql import ARRAY, JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class EmailProviderName(str, enum.Enum):
    gmail = "gmail"
    # Added in Phase 5 (Microsoft Graph) — declared now so the column's enum
    # type doesn't need an ALTER TYPE migration when that provider lands.
    outlook = "outlook"


class EmailAccountStatus(str, enum.Enum):
    active = "active"
    revoked = "revoked"
    # Set when a token refresh fails (user revoked access from their Google
    # account, or the refresh token expired). Distinct from "revoked", which
    # means FinPilot itself disconnected the account — this means the
    # provider side broke and the user needs to reconnect.
    needs_reauth = "needs_reauth"


class EmailAccount(Base):
    """One connected mailbox. The Slack "Installation" equivalent.

    One row per company, same as Installation — a company connects one
    mailbox at a time in Phase 1. company_id is unique for the same reason
    Installation's is: it is the tenancy boundary every query filters by.
    """

    __tablename__ = "email_account"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True, nullable=False)
    provider: Mapped[EmailProviderName] = mapped_column(
        Enum(EmailProviderName, name="email_provider_name"), nullable=False
    )
    email_address: Mapped[str] = mapped_column(String(320), nullable=False)

    # OAuth access tokens expire (typically ~1 hour) and are refreshed using
    # the refresh token — unlike Slack's bot token, which never expires. Both
    # halves are encrypted at rest with the same Fernet approach as Slack.
    access_token_encrypted: Mapped[str] = mapped_column(String, nullable=False)
    refresh_token_encrypted: Mapped[str] = mapped_column(String, nullable=False)
    token_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    scopes: Mapped[list[str]] = mapped_column(
        ARRAY(String).with_variant(JSON(), "sqlite"), nullable=False, default=list
    )
    status: Mapped[EmailAccountStatus] = mapped_column(
        Enum(EmailAccountStatus, name="email_account_status"),
        nullable=False,
        default=EmailAccountStatus.active,
    )

    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Gmail's incremental-sync cursor (historyId) / Outlook's delta link.
    # Lives on the account rather than a separate SyncCursor row because,
    # unlike Slack's per-conversation cursors, Gmail's historyId is a single
    # mailbox-wide checkpoint — one cursor per account, not one per folder.
    # Unbounded, not a narrow VARCHAR — Outlook's delta link (Phase 5) is a
    # full URL with an encoded state token, routinely well past 255
    # characters. Same lesson already learned once with Gmail's
    # attachmentId (see EmailAttachment.provider_attachment_id): widened
    # proactively here instead of waiting to reproduce that exact bug.
    sync_cursor: Mapped[str | None] = mapped_column(String, nullable=True)

    # How far back the first sync looks, in days. Unlike Slack (where None
    # means "all history" and is a safe default — a workspace is bounded),
    # a mailbox can hold a decade of unrelated personal/financial/legal
    # correspondence, so an unbounded first sync is a real privacy and
    # storage risk (docs/email-connector-plan.md §5/§9). None here still
    # means "no explicit choice made yet" — nobody has, since Phase 3's
    # picker UI for email doesn't exist — but the orchestrator applies a
    # conservative default (180 days) rather than treating None as unbounded,
    # unlike Slack's identically-named field.
    sync_window_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
