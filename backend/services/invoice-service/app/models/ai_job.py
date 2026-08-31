import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AIJobStatus(str, enum.Enum):
    queued = "queued"
    processing = "processing"
    done = "done"
    failed = "failed"


class AIJob(Base):
    """Tracks one scan request end to end — architecture report §5.3/§5.8's
    AIJob, kept even though Phase 3 calls AI Engine synchronously (no
    RabbitMQ yet, see config.py) rather than via a queue: `GET
    /invoices/scan/{job_id}` still needs something to poll, and this is the
    same shape a future queue-based version would use, so the API contract
    does not have to change when RabbitMQ eventually lands.
    """

    __tablename__ = "ai_job"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    # Set once extraction succeeds and an Invoice row exists — null while
    # queued/processing, and stays null on a failed job (nothing to point at).
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoice.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[AIJobStatus] = mapped_column(
        Enum(AIJobStatus, name="ai_job_status"), nullable=False, default=AIJobStatus.queued
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
