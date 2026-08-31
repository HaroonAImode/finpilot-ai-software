import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

#: Same set HR Service suggests — a separate copy, not a shared import:
#: each service owns its own suggestions, the same convention
#: vendors-service's category list and expenses' category list already
#: established independently of one another.
SUGGESTED_DEPARTMENTS = (
    "Finance", "Operations", "Sales", "Procurement", "Accounts", "IT", "HR", "Logistics",
)


class PurchaseRequestStatus(str, enum.Enum):
    pending_approval = "pending_approval"
    approved = "approved"
    rejected = "rejected"


class PurchaseRequest(Base):
    """A department's ask to buy something, before any vendor is chosen or
    money is committed. `requester_name` is free text, not a Employee
    foreign key — HR Service owns employee identity in its own database
    (Rule 1), and there is no reconciliation queue for this field in v1
    (see the design spec's non-goals): the Requester column just needs a
    name to display, not a verified link.

    Deliberately has no "delayed" or "cancelled" status, unlike the
    architecture report's original four-value list for this model — both
    describe a delivery running late or being called off, which only make
    sense once a PurchaseOrder exists. See PurchaseOrderStatus and the
    design spec §3 for where those concepts actually live.
    """

    __tablename__ = "purchase_request"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    item_description: Mapped[str] = mapped_column(String(500), nullable=False)
    department: Mapped[str] = mapped_column(String(100), nullable=False)
    requester_name: Mapped[str] = mapped_column(String(300), nullable=False)
    amount_pkr: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[PurchaseRequestStatus] = mapped_column(
        Enum(PurchaseRequestStatus, name="purchase_request_status"),
        nullable=False, default=PurchaseRequestStatus.pending_approval,
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now(),
    )
