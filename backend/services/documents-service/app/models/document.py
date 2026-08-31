import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class DocumentCategory(str, enum.Enum):
    """Mirrors the connectors' own category vocabulary (see
    slack-connector's rules.yaml) so the Documents page can show one
    consistent set of filters across every source. A browser upload has no
    rules engine behind it — the uploader picks, or it stays `other`."""

    invoices = "invoices"
    receipts = "receipts"
    reports = "reports"
    contracts = "contracts"
    images = "images"
    other = "other"


class ScannerStatus(str, enum.Enum):
    """Whether this document has been handed to Invoice Service's scanner.

    Tracked so the Documents page can show that a file already became an
    invoice, instead of the user re-sending the same document repeatedly —
    the exact re-upload loop this service exists to end.
    """

    not_sent = "not_sent"
    sent = "sent"
    failed = "failed"


class Document(Base):
    """One file a user uploaded through the browser.

    Deliberately *not* an Invoice: most uploads (contracts, statements,
    photos) never become one, and a general document library has a
    different lifecycle from extracted invoice data. Sending a document to
    the scanner creates an Invoice in that service; this row is unaffected
    by anything that later happens to that invoice, and vice versa.
    """

    __tablename__ = "document"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    mimetype: Mapped[str | None] = mapped_column(String(255), nullable=True)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    s3_key: Mapped[str] = mapped_column(String(1000), nullable=False)
    #: SHA-256 of the bytes. Not a uniqueness constraint here (unlike
    #: Invoice.file_hash): re-uploading the same file to a document library
    #: is a legitimate thing to do, and silently returning the earlier row
    #: would be surprising. Kept so duplicates can be *surfaced* later.
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    category: Mapped[DocumentCategory] = mapped_column(
        Enum(DocumentCategory, name="document_category"), nullable=False, default=DocumentCategory.other,
    )
    scanner_status: Mapped[ScannerStatus] = mapped_column(
        Enum(ScannerStatus, name="document_scanner_status"), nullable=False, default=ScannerStatus.not_sent,
    )
    #: The Invoice this document produced, when it has been scanned. A plain
    #: UUID, not a real FK — it lives in another service's database, so the
    #: constraint cannot be enforced here (same convention the connectors
    #: use for their own cross-service references).
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    #: Soft delete. Set instead of removing the row, and the stored object is
    #: deliberately left in S3 — this is a financial app, and a document that
    #: was seen, categorised, and possibly turned into a booked invoice should
    #: not be able to vanish without trace. Every list query filters on this.
    #: Reclaiming storage is a separate retention concern that can reason
    #: about what is actually safe to purge.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now(),
    )
