import enum
import uuid
from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, Enum, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ReportType(str, enum.Enum):
    profit_loss = "profit_loss"
    balance_sheet = "balance_sheet"
    cash_flow = "cash_flow"
    tax_summary = "tax_summary"
    sales = "sales"
    purchases = "purchases"


class ReportStatus(str, enum.Enum):
    """Architecture report §5.9's own four-value list, kept for schema
    fidelity — but only `ready` is ever actually persisted in this build.
    Generation is synchronous (see the design spec §2 for why no queue was
    needed): a report either finishes within its own POST request and is
    stored `ready`, or a downstream service fails and the request itself
    returns a 502 with **no** row ever created, rather than a persisted
    `failed` one to retry later. `queued`/`generating`/`failed` are
    declared for a future async migration to grow into, not produced today.
    """

    queued = "queued"
    generating = "generating"
    ready = "ready"
    failed = "failed"


class Report(Base):
    """One generated report. `payload_json` holds the full computed
    result (see schemas/payload.py's ReportPayload) — PDF and Excel are
    rendered from it **on demand** at download time, not pre-rendered to
    a stored file. Architecture report §5.9's own `file_path_pdf`/
    `file_path_excel` columns are therefore not needed here: nothing
    about generating either format is slow enough to justify persisting
    bytes instead of the structured data they're rendered from.
    """

    __tablename__ = "report"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    type: Mapped[ReportType] = mapped_column(Enum(ReportType, name="report_type"), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    #: Equal to period_start for a point-in-time report (Balance Sheet).
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[ReportStatus] = mapped_column(
        Enum(ReportStatus, name="report_status"), nullable=False, default=ReportStatus.ready,
    )
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False)

    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
