import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

#: The categories the architecture report §5.7 names. Exposed for the UI to
#: offer as suggestions, but **not** enforced as a database enum: real
#: suppliers do not fit six buckets, and every new one would otherwise need
#: an ALTER TYPE migration before a user could type it.
SUGGESTED_CATEGORIES = (
    "Raw Material", "Steel", "Packaging", "Fuel", "Transport", "Office",
)

#: Likewise suggestions, not a closed set — §5.7's own "Net 30/Net 45/
#: Advance/etc." says as much.
SUGGESTED_PAYMENT_TERMS = (
    "Net 7", "Net 15", "Net 30", "Net 45", "Net 60", "Advance", "On Delivery",
)


class VendorStatus(str, enum.Enum):
    """A genuinely fixed lifecycle, unlike category and payment terms — so
    this one *is* an enum. "review" means the relationship is under
    question (quality, pricing, a dispute) without being ended."""

    active = "active"
    inactive = "inactive"
    review = "review"


class Vendor(Base):
    """One supplier this company buys from.

    Exists to give vendors a stable identity. Before it, `invoice.vendor_name`
    was free text written from whatever OCR read, so a misread of the same
    supplier became a second permanent "vendor" — and the fuzzy matcher then
    matched future invoices against that polluted list, compounding the error.

    `total_spend_pkr` is deliberately **not** a column here: it is derived
    from Invoice Service on read (see services/invoice_service_client.py).
    A stored aggregate would drift the moment an invoice is corrected in the
    Records grid, and there is no event bus to keep it honest.
    """

    __tablename__ = "vendor"
    __table_args__ = (
        # Two vendors with the same name in one company are almost certainly
        # the duplicate this service exists to prevent. Scoped per company,
        # not global — different companies share supplier names constantly.
        UniqueConstraint("company_id", "name", name="uq_vendor_company_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    name: Mapped[str] = mapped_column(String(500), nullable=False)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    city: Mapped[str | None] = mapped_column(String(200), nullable=True)
    ntn: Mapped[str | None] = mapped_column(String(32), nullable=True)
    payment_terms: Mapped[str | None] = mapped_column(String(100), nullable=True)

    #: 0-5, or NULL when nobody has rated this vendor yet. Nullable rather
    #: than defaulting to 0, because "unrated" and "rated zero" are very
    #: different statements about a supplier.
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)

    status: Mapped[VendorStatus] = mapped_column(
        Enum(VendorStatus, name="vendor_status"), nullable=False, default=VendorStatus.active,
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now(),
    )
