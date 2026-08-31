"""create workspace and installation tables

Revision ID: 20260813_01
Revises:
Create Date: 2026-08-13
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260813_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    installation_status = sa.Enum("active", "revoked", "needs_reauth", name="installation_status")
    op.create_table(
        "workspace",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("slack_team_id", sa.String(length=64), nullable=False, unique=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=True),
        sa.Column("is_enterprise", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_table(
        "installation",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("bot_token_encrypted", sa.String(), nullable=False),
        sa.Column("bot_user_id", sa.String(length=64), nullable=True),
        sa.Column("user_token_encrypted", sa.String(), nullable=True),
        sa.Column("scopes", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("installed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("status", installation_status, nullable=False, server_default="active"),
    )


def downgrade() -> None:
    op.drop_table("installation")
    op.drop_table("workspace")
    sa.Enum(name="installation_status").drop(op.get_bind(), checkfirst=True)
