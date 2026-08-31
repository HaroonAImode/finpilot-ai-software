"""create email_account table

Revision ID: 20260820_01
Revises:
Create Date: 2026-08-20
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260820_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    email_provider_name = sa.Enum("gmail", "outlook", name="email_provider_name")
    email_account_status = sa.Enum("active", "revoked", "needs_reauth", name="email_account_status")

    op.create_table(
        "email_account",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("provider", email_provider_name, nullable=False),
        sa.Column("email_address", sa.String(length=320), nullable=False),
        sa.Column("access_token_encrypted", sa.String(), nullable=False),
        sa.Column("refresh_token_encrypted", sa.String(), nullable=False),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scopes", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("status", email_account_status, nullable=False, server_default="active"),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sync_cursor", sa.String(length=255), nullable=True),
    )
    op.create_index("ix_email_account_email_address", "email_account", ["email_address"])


def downgrade() -> None:
    op.drop_index("ix_email_account_email_address", table_name="email_account")
    op.drop_table("email_account")
    sa.Enum(name="email_account_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="email_provider_name").drop(op.get_bind(), checkfirst=True)
