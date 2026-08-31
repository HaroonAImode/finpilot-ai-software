"""Provider-agnostic mail client contract — Phase 5 (plan §11a).

SyncOrchestrator is the one place a second provider genuinely needs this:
duplicating it instead would mean duplicating a bug-prone state machine —
every one of the four major live bugs docs/email-connector-plan.md's
CHANGELOG records (narrow column, session poisoning, S3 key length,
ephemeral attachmentId) lived inside that one file. Gmail's raw client
(gmail/client.py) is adapted to this shape by gmail/provider.py, which is a
thin wrapper — gmail/client.py itself is untouched, so its own tests keep
exercising the real Gmail wire format directly. Outlook's client
(outlook/client.py) implements this contract directly since it has no
legacy raw shape to preserve.

Thinner call sites — auth.py's OAuth routes, attachment_fetch.py's
on-demand re-fetch, providers/token_manager.py's refresh — deliberately do
NOT go through this abstraction. Each is a short branch on account.provider
calling the right provider's functions directly; forcing three-line
functions through a shared Protocol would add indirection without saving
any real duplication.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import AsyncIterator, Protocol


@dataclass
class NormalizedAttachment:
    """One attachment, at a fixed position in its message.

    `index` — not the provider's own id — is the durable dedup key for both
    providers (see persistence.py). Gmail's id is provably ephemeral
    (regenerated on every fetch); Outlook's is usually stable but the
    contract makes no promise either way, so nothing relies on either
    provider's id for identity, only for locating bytes right now.
    """

    index: int
    filename: str
    mimetype: str | None
    size: int
    provider_ref: str  # opaque handle the provider needs to fetch bytes


@dataclass
class NormalizedMessage:
    provider_message_id: str
    thread_id: str | None
    subject: str | None
    snippet: str | None
    received_at: datetime | None
    # From/Subject/To/Date, in the same shape gmail/client.py's
    # extract_headers already produces — feeds persistence.py's
    # get_or_create_message unchanged, so that file and its tests do not
    # need to know a second provider exists.
    raw_headers: dict[str, str]
    attachments: list[NormalizedAttachment] = field(default_factory=list)


class ProviderHistoryExpired(Exception):
    """The incremental sync cursor is too old for the provider to resolve
    (Gmail: history.list 404s past ~30 days; Outlook: delta 410 Gone past
    its own retention window). Callers fall back to a bounded backfill
    rather than treat this as "nothing changed" — see
    SyncOrchestrator._resolve_message_ids."""


class MailProviderClient(Protocol):
    """One authenticated session against one mailbox — an async context
    manager, matching GmailClient's existing shape."""

    next_cursor: str | None
    """Set by list_message_ids_since/list_message_ids_backfill once it is
    safe to persist as the account's new sync_cursor. Timing is deliberately
    provider-specific: Gmail captures a watermark BEFORE listing, so mail
    arriving mid-sync is caught by the *next* run rather than missed.
    Microsoft Graph's delta protocol is edge-consistent by construction —
    its deltaLink is only meaningful AFTER a full page walk completes, and
    correctly covers anything that changed during that walk. Both are
    correct for their own protocol; forcing one timing model onto the other
    would not be."""

    async def __aenter__(self) -> "MailProviderClient": ...

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None: ...

    async def get_profile(self) -> dict:
        """Returns at least {"email_address": str}."""
        ...

    def list_message_ids_since(self, cursor: str) -> AsyncIterator[str]:
        """Incremental listing. Raises ProviderHistoryExpired if `cursor`
        can no longer be resolved."""
        ...

    def list_message_ids_backfill(self, window_days: int) -> AsyncIterator[str]:
        """Bounded listing for a first sync, or a cursor that expired."""
        ...

    async def get_message(self, message_id: str) -> NormalizedMessage: ...

    async def get_attachment_bytes(self, message_id: str, attachment: NormalizedAttachment) -> bytes: ...
