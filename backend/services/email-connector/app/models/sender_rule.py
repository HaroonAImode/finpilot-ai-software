import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SenderRuleAction(str, enum.Enum):
    allow = "allow"
    deny = "deny"


class SenderRule(Base):
    """A per-account allow/deny rule matched against an attachment's sender.

    docs/email-connector-plan.md §5 calls these out as the single biggest
    filtering quality win — "always import from billing@vendor.com, never
    from newsletter@" — so they are a first-class table rather than a
    config blob.
    """

    __tablename__ = "sender_rule"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("email_account.id", ondelete="CASCADE"), nullable=False
    )
    # Either a full address ("billing@vendor.com") or a bare domain
    # ("vendor.com"), which matches every address at that domain. Stored
    # lower-cased so matching never has to care about how the header was
    # capitalised.
    pattern: Mapped[str] = mapped_column(String(320), nullable=False)
    action: Mapped[SenderRuleAction] = mapped_column(
        Enum(SenderRuleAction, name="sender_rule_action"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
