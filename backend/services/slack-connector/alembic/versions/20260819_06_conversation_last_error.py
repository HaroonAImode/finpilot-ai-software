"""add per-conversation last_error

Revision ID: 20260819_06
Revises: 20260819_05
Create Date: 2026-08-19

Phase 2 of docs/selective-sync-plan.md.

Sync failures were only recorded on the job, as a flat list of strings. That is
enough to know something went wrong and not enough to act: a run reporting
"8 of 10 conversations" gave no way to see *which* two failed or why. Storing
the Slack error code against the conversation is what lets the UI say
"FinPilot isn't in #private-test — invite it" next to the channel itself.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260819_06"
down_revision = "20260819_05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversation", sa.Column("last_error", sa.String(length=128), nullable=True))


def downgrade() -> None:
    op.drop_column("conversation", "last_error")
