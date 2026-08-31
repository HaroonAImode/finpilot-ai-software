import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class File(Base):
    __tablename__ = "file"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    installation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("installation.id", ondelete="CASCADE"), nullable=False)
    slack_file_id: Mapped[str] = mapped_column(String(64), nullable=False)
    conversation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("conversation.id", ondelete="CASCADE"), nullable=False)
    message_ts: Mapped[str] = mapped_column(String(64), nullable=False)
    thread_ts: Mapped[str | None] = mapped_column(String(64), nullable=True)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    file_type: Mapped[str] = mapped_column(String(64), nullable=False)
    mimetype: Mapped[str | None] = mapped_column(String(64), nullable=True)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_external: Mapped[bool] = mapped_column(default=False, nullable=False)
    external_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    url_private_download: Mapped[str | None] = mapped_column(Text, nullable=True)
    shared_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True)
    shared_by_user_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    slack_permalink: Mapped[str] = mapped_column(String(1000), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="uncategorized")
    category_confidence: Mapped[float] = mapped_column(default=0.0, nullable=False)
    category_source: Mapped[str] = mapped_column(String(64), nullable=False, default="unknown")
    sha256_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    s3_key: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    downloaded: Mapped[bool] = mapped_column(default=False, nullable=False)
    download_failed: Mapped[bool] = mapped_column(default=False, nullable=False)
    download_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    indexed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    #: Soft delete — this row mirrors a real Slack message, so deleting it
    #: here must never touch Slack or the S3 copy, only hide it from FinPilot.
    #: The next sync would otherwise just re-discover it, same reasoning as
    #: documents-service's Document.deleted_at.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    conversation: Mapped["Conversation"] = relationship(back_populates="files", foreign_keys=[conversation_id])
    shared_by_user: Mapped["AppUser"] = relationship(back_populates="files", foreign_keys=[shared_by_user_id])
