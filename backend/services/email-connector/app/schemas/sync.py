from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class EmailAttachmentResponse(BaseModel):
    id: UUID
    filename: str
    mimetype: Optional[str]
    size: int
    created_at: datetime
    category: str
    category_confidence: float
    category_source: str
    downloaded: bool
    download_failed: bool
    review_status: str
    review_reason: Optional[str]
    message_id: UUID
    # Denormalised onto the response so the Documents page can show "from
    # whom" without a second lookup per attachment — same reasoning as
    # Slack's FileResponse carrying conversation_id.
    from_name: Optional[str] = None
    from_address: Optional[str] = None
    subject: Optional[str] = None
    received_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class EmailAttachmentListResponse(BaseModel):
    attachments: list[EmailAttachmentResponse]
    total: int
    page: int
    page_size: int


class EmailSyncJobResponse(BaseModel):
    id: UUID
    status: str
    started_at: datetime
    completed_at: Optional[datetime]
    messages_scanned: int
    attachments_discovered: int
    attachments_downloaded: int
    attachments_failed: int
    errors: list[str]

    class Config:
        from_attributes = True


class EmailSyncStartResponse(BaseModel):
    sync_job_id: UUID
    status: str
    message: str


class EmailSyncSettingsRequest(BaseModel):
    """How far back the next (first, or full-resync) sync looks. None means
    the connector's own default (180 days) rather than "all time" — see
    sync_orchestrator.DEFAULT_SYNC_WINDOW_DAYS for why that differs from
    Slack's identically-shaped field."""

    sync_window_days: Optional[int] = Field(default=None, ge=1, le=3650)


class EmailSyncSettingsResponse(BaseModel):
    sync_window_days: Optional[int]


class CategoryUpdateRequest(BaseModel):
    category: str


class SenderRuleResponse(BaseModel):
    id: UUID
    pattern: str
    action: str
    created_at: datetime

    class Config:
        from_attributes = True


class SenderRuleCreateRequest(BaseModel):
    #: A full address ("billing@vendor.com") or a bare domain ("vendor.com").
    pattern: str = Field(min_length=3, max_length=320)
    action: Literal["allow", "deny"]


class ReviewDecisionRequest(BaseModel):
    """Optionally create a sender rule alongside approving/rejecting.

    This is how the allow/deny lists actually get populated — nobody writes
    them up front, they accumulate from decisions made in the tray.
    """

    #: "address" adds a rule for this exact sender, "domain" for its whole
    #: domain, None adds no rule at all.
    also_rule_for: Optional[Literal["address", "domain"]] = None
