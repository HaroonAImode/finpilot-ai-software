import enum
import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum, Float, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PurchaseOrderStatus(str, enum.Enum):
    """Matches architecture report §5.6's own three-value list exactly —
    unlike PurchaseRequestStatus, nothing was dropped here. "Delayed" is
    deliberately **not** a fourth stored value: it is computed
    (`in_transit` and past `expected_delivery`) rather than something a
    human has to remember to set — see `routes/stats.py`."""

    in_transit = "in_transit"
    delivered = "delivered"
    cancelled = "cancelled"


class PurchaseOrder(Base):
    """One order placed with a vendor. `purchase_request_id` is a real
    foreign key (both tables live in this service's own database) but is
    nullable — most orders originate from an approved request (architecture
    report §5.6's "create PO from approved request"), but nothing here
    forces every purchase through that flow.

    `vendor_id`/`vendor_name` follow the same pattern as Expense's own
    vendor fields: `vendor_id` names a record in Vendors Service's
    database (not a foreign key here — Rule 1), `vendor_name` is a
    snapshot for display that survives even if that vendor is never linked.
    """

    __tablename__ = "purchase_order"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    purchase_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_request.id", ondelete="SET NULL"), nullable=True,
    )

    vendor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    vendor_name: Mapped[str] = mapped_column(String(500), nullable=False)

    order_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount_pkr: Mapped[float] = mapped_column(Float, nullable=False)
    expected_delivery: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[PurchaseOrderStatus] = mapped_column(
        Enum(PurchaseOrderStatus, name="purchase_order_status"),
        nullable=False, default=PurchaseOrderStatus.in_transit,
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now(),
    )
