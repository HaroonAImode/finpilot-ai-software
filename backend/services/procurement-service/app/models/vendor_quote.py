import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class VendorQuote(Base):
    """One vendor's bid for a specific purchase request — architecture
    report §5.6's "vendor comparison for active bids," scoped to the
    request it is actually comparing options for rather than a
    company-wide, unscoped list. There is no RFQ/bid-collection workflow
    anywhere in this codebase (no emails sent, no vendor portal) — a quote
    is recorded by a human after getting it by whatever means they already
    use, the same "record it, don't automate soliciting it" scope Vendor
    Reconciliation already applies to fixing up OCR vendor names.

    `score` is a human judgement call (0-100), not a computed ranking —
    inventing a scoring formula from price/delivery/quality would be an
    unexplainable black box for a real purchasing decision. Nullable:
    "not yet scored" is a real, different state from "scored zero."
    """

    __tablename__ = "vendor_quote"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    purchase_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_request.id", ondelete="CASCADE"), nullable=False, index=True,
    )

    #: Not a foreign key — Vendors Service owns this id, in its own
    #: database. Nullable: a quote can come from a supplier not yet in
    #: the vendor directory at all.
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    vendor_name: Mapped[str] = mapped_column(String(500), nullable=False)

    price_pkr: Mapped[float] = mapped_column(Float, nullable=False)
    #: Free text ("3 days", "next week") — matches how this figure is
    #: actually quoted in practice, not a number of days a vendor never
    #: gave precisely.
    delivery_estimate: Mapped[str | None] = mapped_column(String(100), nullable=True)
    quality_rating: Mapped[str | None] = mapped_column(String(50), nullable=True)
    payment_terms: Mapped[str | None] = mapped_column(String(100), nullable=True)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now(),
    )
