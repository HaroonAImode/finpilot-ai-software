import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import ARRAY, JSON, UUID
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class EmailSyncStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"


class EmailSyncJob(Base):
    __tablename__ = "email_sync_job"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("email_account.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[EmailSyncStatus] = mapped_column(
        Enum(EmailSyncStatus, name="email_sync_status"), nullable=False, default=EmailSyncStatus.queued
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    messages_scanned: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attachments_discovered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attachments_downloaded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attachments_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # MutableList so `errors.append(...)` actually persists — a plain ARRAY
    # column silently drops in-place mutation (the exact bug found running
    # the Slack connector's first real sync).
    errors: Mapped[list[str]] = mapped_column(
        MutableList.as_mutable(ARRAY(String).with_variant(JSON(), "sqlite")),
        nullable=False,
        default=list,
    )
