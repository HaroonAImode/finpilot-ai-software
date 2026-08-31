"""add sync window and per-conversation incremental cursor

Revision ID: 20260819_05
Revises: 20260818_04
Create Date: 2026-08-19

Phase 1 of docs/selective-sync-plan.md:

* installation.sync_window_days — how far back a sync looks. NULL keeps the
  current behaviour of scanning all history, so existing installations are
  unaffected until someone chooses a window.
* conversation.last_seen_ts — newest message already processed, so the next sync
  asks Slack only for what is newer instead of re-walking everything. NULL means
  "never synced incrementally", which falls back to a full walk.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260819_05"
down_revision = "20260818_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("installation", sa.Column("sync_window_days", sa.Integer(), nullable=True))
    op.add_column("conversation", sa.Column("last_seen_ts", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("conversation", "last_seen_ts")
    op.drop_column("installation", "sync_window_days")
