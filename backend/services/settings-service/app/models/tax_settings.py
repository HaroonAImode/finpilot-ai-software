import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class FilerStatus(str, enum.Enum):
    """Genuinely a fixed, two-value FBR (Pakistan tax authority)
    classification — unlike vendor/expense category, which are suggestions,
    this one really is a closed set, the same reasoning VendorStatus is an
    enum for being a real fixed lifecycle rather than a suggestion."""

    filer = "filer"
    non_filer = "non_filer"


class TaxSettings(Base):
    """Architecture report §5.10 — "default 18% GST for Pakistan" as this
    project's own stated default, kept here rather than fabricated at
    every call site that needs a rate.

    One row per company, created lazily with that 18% default — see
    routes/tax.py's `_get_or_create`.
    """

    __tablename__ = "tax_settings"
    __table_args__ = (UniqueConstraint("company_id", name="uq_tax_settings_company_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    #: Percentage points (18.0 means 18%), matching how the rest of this
    #: codebase's UI already displays tax rates (e.g. tax_rate * 100 in
    #: Revenue Manager's own form).
    default_gst_rate: Mapped[float] = mapped_column(Float, nullable=False, default=18.0)
    withholding_tax_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    filer_status: Mapped[FilerStatus] = mapped_column(
        Enum(FilerStatus, name="filer_status"), nullable=False, default=FilerStatus.filer,
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now(),
    )
