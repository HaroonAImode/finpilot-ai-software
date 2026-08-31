import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AutomationSettings(Base):
    """AI automation toggles — architecture report §5.10's exact five
    flags. Stored and served real; **not all of them gate real pipeline
    behaviour yet** — see the design spec §3 for exactly which do and
    which are reserved for a future phase that reads this table before
    acting. Persisting the preference now, honestly documented as partly
    inert, is worth more than either fabricating enforcement or not
    building the toggle at all.

    One row per company, created lazily with defaults (all enabled,
    matching the previous dummy UI's own mostly-on defaults) — see
    routes/automation.py's `_get_or_create`.
    """

    __tablename__ = "automation_settings"
    __table_args__ = (UniqueConstraint("company_id", name="uq_automation_settings_company_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    auto_categorize_expenses: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    auto_detect_duplicates: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    auto_insights: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    smart_vendor_suggestions: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    enabled_ai: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now(),
    )
