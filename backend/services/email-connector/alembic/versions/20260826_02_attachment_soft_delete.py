"""add email_attachment.deleted_at for soft delete

Revision ID: 20260826_02
Revises: 20260823_01
Create Date: 2026-08-26

An imported attachment is a copy of a real email — deleting it here must
never touch the mailbox or the S3 object, only hide it from FinPilot.
persist_attachment's upsert never touches this column, so a deleted
attachment stays deleted across re-syncs rather than reappearing.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260826_02"
down_revision = "20260823_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("email_attachment", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("email_attachment", "deleted_at")
