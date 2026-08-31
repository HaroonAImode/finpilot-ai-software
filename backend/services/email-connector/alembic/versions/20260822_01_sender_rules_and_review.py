"""add sender_rule table and email_attachment.review_status/review_reason

Revision ID: 20260822_01
Revises: 20260821_03
Create Date: 2026-08-22
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260822_01"
down_revision = "20260821_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    review_status = sa.Enum("imported", "needs_review", "rejected", name="review_status")
    review_status.create(op.get_bind(), checkfirst=True)

    # Existing rows were all downloaded under the old always-import behaviour,
    # so "imported" is the truthful backfill — they really are in storage.
    op.add_column(
        "email_attachment",
        sa.Column("review_status", review_status, nullable=False, server_default="imported"),
    )
    op.add_column("email_attachment", sa.Column("review_reason", sa.String(length=64), nullable=True))
    op.create_index("ix_email_attachment_review_status", "email_attachment", ["review_status"])

    sender_rule_action = sa.Enum("allow", "deny", name="sender_rule_action")
    op.create_table(
        "sender_rule",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "account_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("email_account.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("pattern", sa.String(length=320), nullable=False),
        sa.Column("action", sender_rule_action, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_sender_rule_account_id", "sender_rule", ["account_id"])
    # One rule per pattern per account — re-adding the same sender should
    # update its action, not silently create a second contradictory row.
    op.create_unique_constraint("uq_sender_rule_account_pattern", "sender_rule", ["account_id", "pattern"])


def downgrade() -> None:
    op.drop_constraint("uq_sender_rule_account_pattern", "sender_rule", type_="unique")
    op.drop_index("ix_sender_rule_account_id", table_name="sender_rule")
    op.drop_table("sender_rule")
    sa.Enum(name="sender_rule_action").drop(op.get_bind(), checkfirst=True)

    op.drop_index("ix_email_attachment_review_status", table_name="email_attachment")
    op.drop_column("email_attachment", "review_reason")
    op.drop_column("email_attachment", "review_status")
    sa.Enum(name="review_status").drop(op.get_bind(), checkfirst=True)
