"""add phase 2 tables: conversation, app_user, file, sync_job, sync_cursor

Revision ID: 20260813_02
Revises: 20260813_01
Create Date: 2026-08-13
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260813_02"
down_revision = "20260813_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conversation_type = sa.Enum("public", "private", "im", "mpim", name="conversation_type")
    sync_status = sa.Enum("queued", "running", "completed", "failed", name="sync_status")

    op.create_table(
        "conversation",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("installation.id", ondelete="CASCADE"), nullable=False),
        sa.Column("slack_conversation_id", sa.String(length=64), nullable=False),
        sa.Column("conversation_type", conversation_type, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("topic", sa.String(length=500), nullable=True),
        sa.Column("description", sa.String(length=1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("last_synced", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sync_complete", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.create_table(
        "app_user",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("installation.id", ondelete="CASCADE"), nullable=False),
        sa.Column("slack_user_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("avatar_url", sa.String(length=1000), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "file",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("installation.id", ondelete="CASCADE"), nullable=False),
        sa.Column("slack_file_id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("conversation.id", ondelete="CASCADE"), nullable=False),
        sa.Column("message_ts", sa.String(length=64), nullable=False),
        sa.Column("thread_ts", sa.String(length=64), nullable=True),
        sa.Column("filename", sa.String(length=500), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("file_type", sa.String(length=64), nullable=False),
        sa.Column("mimetype", sa.String(length=64), nullable=True),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_external", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("external_type", sa.String(length=64), nullable=True),
        sa.Column("url_private_download", sa.Text(), nullable=True),
        sa.Column("shared_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("shared_by_user_name", sa.String(length=255), nullable=True),
        sa.Column("slack_permalink", sa.String(length=1000), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False, server_default="uncategorized"),
        sa.Column("category_confidence", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("category_source", sa.String(length=64), nullable=False, server_default="unknown"),
        sa.Column("sha256_hash", sa.String(length=64), nullable=True),
        sa.Column("s3_key", sa.String(length=1000), nullable=True),
        sa.Column("downloaded", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("download_failed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("download_error", sa.Text(), nullable=True),
        sa.Column("raw_json", postgresql.JSON(), nullable=True),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "sync_job",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("installation.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sync_status, nullable=False, server_default="queued"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_conversations", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("conversations_processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("files_discovered", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("files_downloaded", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("files_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("errors", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("error_details", postgresql.JSON(), nullable=True),
    )

    op.create_table(
        "sync_cursor",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("sync_job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sync_job.id", ondelete="CASCADE"), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("conversation.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cursor", sa.String(), nullable=True),
        sa.Column("is_complete", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_cursor_update", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("sync_cursor")
    op.drop_table("sync_job")
    op.drop_table("file")
    op.drop_table("app_user")
    op.drop_table("conversation")
    sa.Enum(name="sync_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="conversation_type").drop(op.get_bind(), checkfirst=True)
