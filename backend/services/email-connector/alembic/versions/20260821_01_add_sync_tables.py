"""add email_message, email_attachment, email_sync_job; add email_account.sync_window_days

Revision ID: 20260821_01
Revises: 20260820_01
Create Date: 2026-08-21
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260821_01"
down_revision = "20260820_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("email_account", sa.Column("sync_window_days", sa.Integer(), nullable=True))

    op.create_table(
        "email_message",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "account_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("email_account.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("provider_message_id", sa.String(length=128), nullable=False),
        sa.Column("thread_id", sa.String(length=128), nullable=True),
        sa.Column("subject", sa.String(length=998), nullable=True),
        sa.Column("from_address", sa.String(length=320), nullable=True),
        sa.Column("from_name", sa.String(length=255), nullable=True),
        sa.Column("to_address", sa.String(length=320), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("has_attachments", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("snippet", sa.Text(), nullable=True),
        sa.Column("raw_headers_json", postgresql.JSON(), nullable=True),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_email_message_account_id", "email_message", ["account_id"])
    # A given Gmail message id is unique within a mailbox — this is the
    # dedup key a re-sync upserts against, same role as Slack's
    # (installation_id, slack_file_id).
    op.create_unique_constraint(
        "uq_email_message_account_provider_id", "email_message", ["account_id", "provider_message_id"]
    )

    op.create_table(
        "email_attachment",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "account_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("email_account.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "message_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("email_message.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("provider_attachment_id", sa.String(length=256), nullable=False),
        sa.Column("filename", sa.String(length=500), nullable=False),
        sa.Column("mimetype", sa.String(length=255), nullable=True),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False, server_default="uncategorized"),
        sa.Column("category_confidence", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("category_source", sa.String(length=64), nullable=False, server_default="unknown"),
        sa.Column("sha256_hash", sa.String(length=64), nullable=True),
        sa.Column("s3_key", sa.String(length=1000), nullable=True),
        sa.Column("downloaded", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("download_failed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("download_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_email_attachment_account_id", "email_attachment", ["account_id"])
    op.create_index("ix_email_attachment_message_id", "email_attachment", ["message_id"])

    email_sync_status = sa.Enum("queued", "running", "completed", "failed", name="email_sync_status")
    op.create_table(
        "email_sync_job",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "account_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("email_account.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("status", email_sync_status, nullable=False, server_default="queued"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("messages_scanned", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attachments_discovered", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attachments_downloaded", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attachments_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("errors", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}"),
    )
    op.create_index("ix_email_sync_job_account_id", "email_sync_job", ["account_id"])


def downgrade() -> None:
    op.drop_table("email_sync_job")
    sa.Enum(name="email_sync_status").drop(op.get_bind(), checkfirst=True)
    op.drop_index("ix_email_attachment_message_id", table_name="email_attachment")
    op.drop_index("ix_email_attachment_account_id", table_name="email_attachment")
    op.drop_table("email_attachment")
    op.drop_constraint("uq_email_message_account_provider_id", "email_message", type_="unique")
    op.drop_index("ix_email_message_account_id", table_name="email_message")
    op.drop_table("email_message")
    op.drop_column("email_account", "sync_window_days")
