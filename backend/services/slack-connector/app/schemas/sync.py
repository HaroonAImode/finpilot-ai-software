from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class FileResponse(BaseModel):
    id: UUID
    slack_file_id: str
    filename: str
    title: Optional[str]
    file_type: str
    mimetype: Optional[str]
    size: int
    created_at: datetime
    is_external: bool
    category: str
    category_confidence: float
    category_source: str
    downloaded: bool
    download_failed: bool
    shared_by_user_name: Optional[str]
    slack_permalink: str
    conversation_id: Optional[UUID]

    class Config:
        from_attributes = True


class SyncJobResponse(BaseModel):
    id: UUID
    status: str
    started_at: datetime
    completed_at: Optional[datetime]
    total_conversations: int
    conversations_processed: int
    files_discovered: int
    files_downloaded: int
    files_failed: int
    errors: list[str]

    class Config:
        from_attributes = True


class SyncStartResponse(BaseModel):
    sync_job_id: UUID
    status: str
    message: str


class FileListResponse(BaseModel):
    files: list[FileResponse]
    total: int
    page: int
    page_size: int


class CategoryUpdateRequest(BaseModel):
    category: str


class SyncSettingsRequest(BaseModel):
    """How far back syncs should look. None means all history."""

    sync_window_days: Optional[int] = Field(
        default=None,
        ge=1,
        le=3650,
        description="Days of history to scan. Omit or null for everything.",
    )


class SyncSettingsResponse(BaseModel):
    sync_window_days: Optional[int]


# Slack's error codes are stable but opaque to a user. Mapping them here means
# the UI renders advice rather than jargon, and a code we have not seen before
# still produces something honest instead of a blank.
_ERROR_HINTS = {
    "not_in_channel": "FinPilot is not in this channel. Invite it to sync files from here.",
    "channel_not_found": "This conversation is no longer reachable — it may have been deleted or archived.",
    "missing_scope": "FinPilot is missing a Slack permission needed to read this conversation.",
    "is_archived": "This channel is archived, so no new files will appear.",
    "account_inactive": "The Slack account that connected FinPilot is no longer active.",
    "ratelimited": "Slack rate-limited the last sync. The next run will pick up where it stopped.",
}


class ConversationResponse(BaseModel):
    id: UUID
    slack_conversation_id: str
    name: str
    conversation_type: str
    #: Files already synced from here — not Slack's total, which would need an
    #: extra API call per conversation.
    file_count: int
    last_synced: Optional[datetime]
    #: Raw Slack error code, kept so the client can branch on it if it wants.
    last_error: Optional[str]
    #: The same failure in words a user can act on.
    last_error_hint: Optional[str]

    class Config:
        from_attributes = True

    @staticmethod
    def hint_for(code: Optional[str]) -> Optional[str]:
        if not code:
            return None
        return _ERROR_HINTS.get(code, f"Last sync failed for this conversation: {code}")


class ConversationListResponse(BaseModel):
    conversations: list[ConversationResponse]
    total: int
    #: Conversations whose last sync failed — what the UI badges for attention.
    needs_attention: int


class SyncStartRequest(BaseModel):
    """Optional scope for a single sync run.

    Both fields are one-off: they apply to this triggered run and, for
    window_days, are also saved as the installation's new default (matching
    PUT /sync/settings) — but conversation_ids is never persisted as a
    standing scope. There is no scheduler yet that would re-read a saved
    selection, so persisting it would be state nothing ever uses.
    """

    conversation_ids: Optional[list[UUID]] = Field(
        default=None,
        description="Sync only these conversations. Omitted or empty means every conversation.",
    )
    window_days: Optional[int] = Field(
        default=None, ge=1, le=3650,
        description="Override the sync window for this run (and save it as the new default). "
                     "Omit the field entirely to leave the current setting untouched.",
    )
