import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import ARRAY, JSON, UUID
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class SyncStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"


class SyncJob(Base):
    __tablename__ = "sync_job"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    installation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("installation.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[SyncStatus] = mapped_column(Enum(SyncStatus, name="sync_status"), nullable=False, default=SyncStatus.queued)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    total_conversations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    conversations_processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    files_discovered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    files_downloaded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    files_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # MutableList so `errors.append(...)` is actually persisted. With a plain ARRAY
    # column SQLAlchemy never sees in-place list mutation, so the orchestrator's
    # appends were silently dropped: a sync that hit not_in_channel on two private
    # channels still reported errors=[], leaving the user with "8/10 conversations"
    # and no way to learn the bot simply needed inviting.
    errors: Mapped[list[str]] = mapped_column(
        MutableList.as_mutable(ARRAY(String).with_variant(JSON(), "sqlite")),
        nullable=False,
        default=list,
    )
    error_details: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    sync_cursors: Mapped[list["SyncCursor"]] = relationship(back_populates="sync_job", cascade="all, delete-orphan")
