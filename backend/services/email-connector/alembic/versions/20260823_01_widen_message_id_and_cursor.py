"""widen email_message.provider_message_id and email_account.sync_cursor —
Phase 5 (Outlook): Microsoft Graph message ids and delta-link cursors are
both opaque tokens that can run well past the narrow VARCHAR sizes these
columns were originally given, the same class of bug already hit once with
Gmail's provider_attachment_id (see 20260821_02). Widened proactively before
Outlook can ever write to these columns, not reactively after a real sync
fails.

Revision ID: 20260823_01
Revises: 20260822_01
Create Date: 2026-08-23
"""
from alembic import op
import sqlalchemy as sa

revision = "20260823_01"
down_revision = "20260822_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "email_message", "provider_message_id",
        existing_type=sa.String(length=128), type_=sa.String(), existing_nullable=False,
    )
    op.alter_column(
        "email_account", "sync_cursor",
        existing_type=sa.String(length=255), type_=sa.String(), existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "email_account", "sync_cursor",
        existing_type=sa.String(), type_=sa.String(length=255), existing_nullable=True,
    )
    op.alter_column(
        "email_message", "provider_message_id",
        existing_type=sa.String(), type_=sa.String(length=128), existing_nullable=False,
    )
