"""widen email_attachment.provider_attachment_id — Gmail's attachmentId
regularly runs past 250+ characters, found live on the first real sync

Revision ID: 20260821_02
Revises: 20260821_01
Create Date: 2026-08-21
"""
from alembic import op
import sqlalchemy as sa

revision = "20260821_02"
down_revision = "20260821_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "email_attachment", "provider_attachment_id",
        existing_type=sa.String(length=256), type_=sa.String(), existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "email_attachment", "provider_attachment_id",
        existing_type=sa.String(), type_=sa.String(length=256), existing_nullable=False,
    )
