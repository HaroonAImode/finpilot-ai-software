import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class IgnoredVendorName(Base):
    """A raw `vendor_name` string an accountant has decided is not a vendor
    at all — a scanner fallback string ("Receipt", "INVOICE Invoice #: …"),
    a misread field, a one-off test document. See docs/documents-and-vendors-
    plan.md §3.8: real data surfaced these before this table did.

    Recorded so the same string never resurfaces in the reconciliation
    queue. Deliberately keyed on the exact raw string, not a pattern or a
    vendor id — "ignore this occurrence" is a much safer default than
    "ignore anything similar", and the accountant can always un-ignore one
    row if a real vendor's name is caught by it.
    """

    __tablename__ = "ignored_vendor_name"
    __table_args__ = (
        UniqueConstraint("company_id", "vendor_name", name="uq_ignored_vendor_name_company_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    vendor_name: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
