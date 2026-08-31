"""add file.deleted_at for soft delete

Revision ID: 20260826_07
Revises: 20260819_06
Create Date: 2026-08-26

A Slack file mirrors a real message — deleting our copy must never touch
Slack or the S3 object, only hide it from FinPilot. Re-syncing never clears
this column (discovery.py's upsert only touches the fields it lists), so a
deleted file stays deleted rather than reappearing on the next sync.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260826_07"
down_revision = "20260819_06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("file", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("file", "deleted_at")
