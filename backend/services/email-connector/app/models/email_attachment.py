import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class ReviewStatus(str, enum.Enum):
    #: Passed the filters — bytes are downloaded and it appears in Documents.
    imported = "imported"
    #: Passed the cheap filters but not confidently financial. Metadata only:
    #: the bytes are deliberately NOT downloaded until a human approves, so an
    #: unapproved attachment is never copied into FinPilot's storage at all.
    needs_review = "needs_review"
    #: A human said no. Kept as a row so the next sync does not re-offer it.
    rejected = "rejected"


class EmailAttachment(Base):
    """One attachment. The Slack "File" equivalent.

    Note that a row existing does not mean the file was downloaded — see
    ReviewStatus.needs_review, which is metadata-only by design.
    """

    __tablename__ = "email_attachment"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("email_account.id", ondelete="CASCADE"), nullable=False
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("email_message.id", ondelete="CASCADE"), nullable=False
    )
    # Position of this attachment within its message's MIME tree, in
    # document order. This — not provider_attachment_id — is the stable
    # half of the dedup key, because Gmail regenerates attachmentId on
    # every fetch (see below).
    attachment_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Gmail's attachmentId: an opaque token, routinely 300-400+ characters
    # (hence unbounded — a real sync hit StringDataRightTruncationError on a
    # VARCHAR(256)), and **ephemeral** — regenerated on every messages.get
    # call for the same unchanged attachment. Stored only so a download can
    # use it within the same run; it must never be treated as an identity.
    # Found live: keying dedup on it produced a complete duplicate set of
    # rows on the second sync.
    provider_attachment_id: Mapped[str] = mapped_column(String, nullable=False)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    mimetype: Mapped[str | None] = mapped_column(String(255), nullable=True)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="uncategorized")
    category_confidence: Mapped[float] = mapped_column(default=0.0, nullable=False)
    category_source: Mapped[str] = mapped_column(String(64), nullable=False, default="unknown")
    review_status: Mapped[ReviewStatus] = mapped_column(
        Enum(ReviewStatus, name="review_status"), nullable=False, default=ReviewStatus.imported
    )
    #: Short machine-readable note on why this landed in needs_review
    #: ("low_confidence"), so the tray can explain itself without guessing.
    review_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sha256_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    s3_key: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    downloaded: Mapped[bool] = mapped_column(default=False, nullable=False)
    download_failed: Mapped[bool] = mapped_column(default=False, nullable=False)
    download_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    #: Soft delete — distinct from ReviewStatus.rejected above. Rejected means
    #: "never approved for download"; this means "was imported, then a user
    #: removed it from view." Deleting here must never touch the mailbox or
    #: the S3 copy, only hide it — the next sync would otherwise just
    #: re-offer or re-import it, same reasoning as Slack's File.deleted_at.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    message: Mapped["EmailMessage"] = relationship(back_populates="attachments")
