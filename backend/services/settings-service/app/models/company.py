import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class LogoPlacement(str, enum.Enum):
    """Where the logo sits in the invoice header row — a real, fixed
    three-value layout choice, not a suggestion list."""

    left = "left"
    center = "center"
    right = "right"


class InvoiceTemplate(str, enum.Enum):
    """The four sales-invoice PDF/preview templates — see
    invoice-service's `sales_pdf.py` for the ReportLab side and the
    frontend's `invoice-templates.tsx` for the live-preview side. Both
    read this same value, so a company's choice looks the same whether
    they're looking at the browser preview or the downloaded PDF."""

    classic = "classic"
    modern = "modern"
    midnight = "midnight"
    minimal = "minimal"


class Company(Base):
    """The extended company profile — architecture report §5.10. Deliberately
    separate from Auth Service's own `company_name` (set once at signup,
    read-only, identity-scoped): this table holds the fields nothing else
    owns yet — NTN, address, industry, a logo, contact details — editable
    any time. The two names living in two services is an accepted overlap,
    not synchronised: this table's `name` starts null rather than copying
    Auth's value, so the two are never presented as the same fact when
    they might quietly diverge.

    One row per company, created lazily with defaults on first read/write —
    see routes/company.py's `_get_or_create` — since nothing in Auth
    Service provisions a row here at signup.
    """

    __tablename__ = "company"
    __table_args__ = (UniqueConstraint("company_id", name="uq_company_company_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    ntn: Mapped[str | None] = mapped_column(String(32), nullable=True)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    city: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: A data: URI, not a hosted file URL — see the design spec's own
    #: reasoning for why this service has no object storage of its own.
    #: Widened from String(1000) to Text: a small PNG/JPEG logo's base64
    #: payload runs to tens of thousands of characters, well past what a
    #: bounded varchar was ever meant to hold.
    logo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    logo_placement: Mapped[LogoPlacement] = mapped_column(
        Enum(LogoPlacement, name="logo_placement"), nullable=False, default=LogoPlacement.left,
    )
    invoice_template: Mapped[InvoiceTemplate] = mapped_column(
        Enum(InvoiceTemplate, name="invoice_template"), nullable=False, default=InvoiceTemplate.classic,
    )
    industry: Mapped[str | None] = mapped_column(String(200), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    email: Mapped[str | None] = mapped_column(String(300), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now(),
    )
