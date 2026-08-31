import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class ConversationType(str, enum.Enum):
    public = "public"
    private = "private"
    im = "im"
    mpim = "mpim"


class Conversation(Base):
    __tablename__ = "conversation"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    installation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("installation.id", ondelete="CASCADE"), nullable=False)
    slack_conversation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    conversation_type: Mapped[ConversationType] = mapped_column(Enum(ConversationType, name="conversation_type"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    topic: Mapped[str | None] = mapped_column(String(500), nullable=True)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_synced: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Newest message timestamp already processed for this conversation. The next
    # sync asks Slack only for what is newer, instead of re-walking the whole
    # history and re-downloading files it already has.
    last_seen_ts: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Why this conversation last failed, as the raw Slack error code
    # ("not_in_channel", "channel_not_found"). Kept per conversation because a
    # job-level error list cannot say *which* channel needs the bot invited —
    # which is the one thing the user needs in order to fix it.
    last_error: Mapped[str | None] = mapped_column(String(128), nullable=True)

    sync_complete: Mapped[bool] = mapped_column(default=False, nullable=False)

    files: Mapped[list["File"]] = relationship(back_populates="conversation", cascade="all, delete-orphan")
    sync_cursors: Mapped[list["SyncCursor"]] = relationship(back_populates="conversation", cascade="all, delete-orphan")
