import uuid

from sqlalchemy import JSON, ARRAY, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class InvoiceItem(Base):
    __tablename__ = "invoice_item"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoice.id", ondelete="CASCADE"), nullable=False
    )
    #: Preserves the line-item table's original row order — display order
    #: matters for a human reviewing against the source document.
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    qty: Mapped[float | None] = mapped_column(Float, nullable=True)
    rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    amount: Mapped[float | None] = mapped_column(Float, nullable=True)

    #: "pass" | "fail" | "not_checked" — Rule 5.1, carried straight through
    #: from the extraction engine's own per-row arithmetic check.
    arithmetic_check: Mapped[str] = mapped_column(String(16), nullable=False, default="not_checked")
    review_flags: Mapped[list[str]] = mapped_column(
        MutableList.as_mutable(ARRAY(String).with_variant(JSON(), "sqlite")), nullable=False, default=list,
    )

    invoice: Mapped["Invoice"] = relationship(back_populates="items")
