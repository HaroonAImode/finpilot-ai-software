"""create ignored_vendor_name table

Revision ID: 20260826_02
Revises: 20260826_01
Create Date: 2026-08-26
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260826_02"
down_revision = "20260826_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ignored_vendor_name",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vendor_name", sa.String(500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_ignored_vendor_name_company_id", "ignored_vendor_name", ["company_id"])
    op.create_unique_constraint(
        "uq_ignored_vendor_name_company_name", "ignored_vendor_name", ["company_id", "vendor_name"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_ignored_vendor_name_company_name", "ignored_vendor_name", type_="unique")
    op.drop_index("ix_ignored_vendor_name_company_id", table_name="ignored_vendor_name")
    op.drop_table("ignored_vendor_name")
