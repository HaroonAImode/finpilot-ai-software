import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import ARRAY, JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class InstallationStatus(str, enum.Enum):
    active = "active"
    revoked = "revoked"
    needs_reauth = "needs_reauth"


class Installation(Base):
    __tablename__ = "installation"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True, nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workspace.id", ondelete="CASCADE"), unique=True, nullable=False)
    bot_token_encrypted: Mapped[str] = mapped_column(String, nullable=False)
    bot_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_token_encrypted: Mapped[str | None] = mapped_column(String, nullable=True)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String).with_variant(JSON(), "sqlite"), nullable=False, default=list)
    # How far back a sync looks, in days. None means all history.
    # Most businesses only care about the current financial year, and scanning
    # years of Slack to find this year's invoices is the single biggest source
    # of wasted API calls and storage.
    sync_window_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    status: Mapped[InstallationStatus] = mapped_column(Enum(InstallationStatus, name="installation_status"), nullable=False, default=InstallationStatus.active)

    workspace: Mapped["Workspace"] = relationship(back_populates="installation")
